/*
 * mod-ascension-ca — AscensionCA.cpp
 *
 * Character Advancement for the stock-client port of Ascension's Conquest of Azeroth
 * on AzerothCore. The client has no custom opcodes, so everything rides on addon chat:
 *
 *     C->S  SendAddonMessage("ASC", "CMD\t<verb>[\t<body>]", "WHISPER", me)
 *     S->C  CHAT_MSG_WHISPER / LANG_ADDON with the player as sender and target,
 *           message "ASC\t<verb>[\t<body>]"  (the client raises CHAT_MSG_ADDON "ASC")
 *
 * Long bodies travel as "<verb>\t#\t<seq>\t<count>\t<chunk>" in both directions.
 *
 * Verbs (all fields tab separated):
 *     HELLO                      -> HELLO\t<protocol>\t<mode>\t<enabled>
 *     PING                       -> PONG
 *     LOG\t<text...>             client diagnostics into the server log
 *     STATE                      -> STATE\t<classByte>\t<specId>\t<ae>\t<te>\t<level>\t<entry:rank,...>
 *     APPLY\t<entry:rank,...>    the COMPLETE wanted set (never a delta; the server replaces)
 *                                -> RESULT\tAPPLY\tOK | RESULT\tAPPLY\tERR\t<reason>\t<entry>, then STATE
 *     SPEC\t<id>                 activate a specialization of the character's CoA class
 *                                -> RESULT\tSPEC\t..., then STATE
 *     RESET\ttalents|all         -> RESULT\tRESET\tOK, then STATE
 *
 * Rules (CoA model, the one the original panel displays): one point per rank; entries
 * of the "Class" tab spend AE, entries of the active specialization's tab spend TE;
 * budgets come from ascension_ca_essence(family = CoA class byte, level); RequiredIDs are
 * prerequisites (must be in the set), RequiredLevel and the Required*Investment columns
 * gate. Learning rank r grants the entry's rank spells 1..r (AzerothCore's own rank
 * chains supersede where Spell.dbc says so); dropping to rank r removes the higher ones.
 *
 * Data: ascension_ca_* world tables from tools/gen_server_sql.py; per-character state in
 * ascension_ca_character / ascension_ca_known (characters database).
 */

#include "ScriptMgr.h"
#include "PlayerScript.h"
#include "WorldScript.h"
#include "Config.h"
#include "Chat.h"
#include "Player.h"
#include "Log.h"
#include "SharedDefines.h"
#include "WorldPacket.h"
#include "DatabaseEnv.h"
#include "GameTime.h"
#include "SpellMgr.h"
#include "DBCStructure.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <map>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace AscensionCA
{
    constexpr uint32 PROTOCOL = 2;
    constexpr std::string_view PREFIX = "ASC\t";
    constexpr std::string_view CMD = "ASC\tCMD\t";
    constexpr size_t MAX_BODY = 200;

    struct Settings
    {
        bool enabled = true;
        std::string mode = "coa";
        bool debug = false;
        uint8 defaultClassByte = 28;
    };
    Settings g;

    struct Entry
    {
        uint32 id = 0;
        uint32 classType = 0;
        std::string className;
        std::string tabName;
        std::string tabUpper;
        bool classTab = false;
        std::string type;
        std::string name;
        uint8 maxRank = 1;
        uint32 reqLevel = 0;
        uint32 reqTabAe = 0, reqTabTe = 0, reqClassAe = 0, reqClassTe = 0, reqClassPoints = 0;
        std::vector<uint32> spells;   // index = rank - 1
        std::vector<uint32> required; // resolved RequiredIDs
    };
    struct ClassRow
    {
        uint8 byte = 0;
        std::string token;
        std::string name;
        uint32 caType = 0;
        std::string caName;
        uint8 powerType = 0;    // ChrClasses DisplayPower: 0 mana, 1 rage, 2 focus, 3 energy, 6 runic power
        uint8 carrier = 0;      // preferred stock class for that power bar
    };
    struct Archetype
    {
        std::string uuid;
        std::string name;
        uint32 classType = 0;
        std::vector<std::pair<uint32, uint8>> entries; // build order
    };
    struct Spec
    {
        uint32 id = 0;
        std::string classToken;
        std::string specToken; // matches an entry's tab name, case-insensitively
        std::string name;
        uint32 passiveSpell = 0;
    };
    struct Budget { uint32 ae = 0, te = 0; };

    std::unordered_map<uint32, Entry> entries;
    std::unordered_map<uint8, ClassRow> classes;
    std::unordered_map<uint32, Spec> specs;
    std::map<uint8, std::map<uint8, Budget>> essence; // family -> level -> budget
    std::unordered_map<std::string, Archetype> archetypes;
    bool dataLoaded = false;

    struct PlayerState
    {
        uint8 classByte = 0;
        uint32 specId = 0;
        bool chosen = false;           // the CoA class was chosen (glue chooser / CLASS verb), not defaulted
        std::string archetype;         // archetype build still being applied on level-up ("" = none)
        std::map<uint32, uint8> known; // entry -> rank
        std::map<std::string, std::map<uint32, std::string>> inbound; // verb -> seq -> chunk
        std::map<std::string, uint32> inboundCount;
    };
    std::unordered_map<ObjectGuid::LowType, PlayerState> players;

    std::string Upper(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        return s;
    }

    void Load()
    {
        g.enabled = sConfigMgr->GetOption<bool>("AscensionCA.Enable", true);
        g.mode = sConfigMgr->GetOption<std::string>("AscensionCA.Mode", "coa");
        g.debug = sConfigMgr->GetOption<bool>("AscensionCA.Debug", false);
        g.defaultClassByte = static_cast<uint8>(sConfigMgr->GetOption<uint32>("AscensionCA.DefaultClassByte", 28));
        LOG_INFO("module", "mod-ascension-ca: enabled={} mode={} protocol={} defaultClassByte={}", g.enabled, g.mode, PROTOCOL, g.defaultClassByte);
    }

    void LoadData()
    {
        entries.clear(); classes.clear(); specs.clear(); essence.clear();
        if (QueryResult r = WorldDatabase.Query("SELECT class_byte, token, name, ca_class_type, ca_class_name, power_type, carrier_class FROM ascension_ca_class"))
        {
            do
            {
                Field* f = r->Fetch();
                ClassRow c;
                c.byte = f[0].Get<uint8>(); c.token = f[1].Get<std::string>(); c.name = f[2].Get<std::string>();
                c.caType = f[3].Get<uint32>(); c.caName = f[4].Get<std::string>();
                c.powerType = f[5].Get<uint8>(); c.carrier = f[6].Get<uint8>();
                classes[c.byte] = c;
            } while (r->NextRow());
        }
        archetypes.clear();
        if (QueryResult r = WorldDatabase.Query("SELECT uuid, name, class_type FROM ascension_ca_archetype"))
        {
            do
            {
                Field* f = r->Fetch();
                Archetype a;
                a.uuid = f[0].Get<std::string>(); a.name = f[1].Get<std::string>(); a.classType = f[2].Get<uint32>();
                archetypes[a.uuid] = a;
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT uuid, entry, `rank` FROM ascension_ca_archetype_entry ORDER BY uuid, ordinal"))
        {
            do
            {
                Field* f = r->Fetch();
                auto it = archetypes.find(f[0].Get<std::string>());
                if (it != archetypes.end())
                    it->second.entries.emplace_back(f[1].Get<uint32>(), f[2].Get<uint8>());
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT id, class_token, spec_token, name, passive_spell FROM ascension_ca_spec"))
        {
            do
            {
                Field* f = r->Fetch();
                Spec s;
                s.id = f[0].Get<uint32>(); s.classToken = f[1].Get<std::string>(); s.specToken = Upper(f[2].Get<std::string>());
                s.name = f[3].Get<std::string>(); s.passiveSpell = f[4].Get<uint32>();
                specs[s.id] = s;
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT family, level, ae, te FROM ascension_ca_essence"))
        {
            do
            {
                Field* f = r->Fetch();
                essence[f[0].Get<uint8>()][f[1].Get<uint8>()] = Budget{ f[2].Get<uint32>(), f[3].Get<uint32>() };
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT entry, class_type, class_name, tab_name, is_class_tab, entry_type, name, max_rank, req_level, req_tab_ae, req_tab_te, req_class_ae, req_class_te, req_class_points FROM ascension_ca_entry"))
        {
            do
            {
                Field* f = r->Fetch();
                Entry e;
                e.id = f[0].Get<uint32>(); e.classType = f[1].Get<uint32>(); e.className = f[2].Get<std::string>();
                e.tabName = f[3].Get<std::string>(); e.tabUpper = Upper(e.tabName); e.classTab = f[4].Get<uint8>() != 0;
                e.type = f[5].Get<std::string>(); e.name = f[6].Get<std::string>(); e.maxRank = std::max<uint8>(1, f[7].Get<uint8>());
                e.reqLevel = f[8].Get<uint32>(); e.reqTabAe = f[9].Get<uint32>(); e.reqTabTe = f[10].Get<uint32>();
                e.reqClassAe = f[11].Get<uint32>(); e.reqClassTe = f[12].Get<uint32>(); e.reqClassPoints = f[13].Get<uint32>();
                entries[e.id] = e;
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT entry, rank_index, spell FROM ascension_ca_entry_spell ORDER BY entry, rank_index"))
        {
            do
            {
                Field* f = r->Fetch();
                auto it = entries.find(f[0].Get<uint32>());
                if (it == entries.end())
                    continue;
                uint8 rank = f[1].Get<uint8>();
                if (it->second.spells.size() < rank)
                    it->second.spells.resize(rank, 0);
                it->second.spells[rank - 1] = f[2].Get<uint32>();
            } while (r->NextRow());
        }
        if (QueryResult r = WorldDatabase.Query("SELECT entry, target FROM ascension_ca_entry_link WHERE kind = 'req' AND resolved = 1"))
        {
            do
            {
                Field* f = r->Fetch();
                auto it = entries.find(f[0].Get<uint32>());
                if (it != entries.end())
                    it->second.required.push_back(f[1].Get<uint32>());
            } while (r->NextRow());
        }
        dataLoaded = !entries.empty();
        LOG_INFO("module", "mod-ascension-ca: {} entries, {} classes, {} specializations, {} essence families", entries.size(), classes.size(), specs.size(), essence.size());
    }

    void Send(Player* player, std::string const& body)
    {
        if (!player)
            return;
        std::string payload;
        payload.reserve(PREFIX.size() + body.size());
        payload.append(PREFIX);
        payload.append(body);
        WorldPacket data;
        ChatHandler::BuildChatPacket(data, CHAT_MSG_WHISPER, payload, LANG_ADDON, CHAT_TAG_NONE,
                                     player->GetGUID(), player->GetName(),
                                     player->GetGUID(), player->GetName());
        player->SendDirectMessage(&data);
        if (g.debug)
            LOG_INFO("module", "ASC -> {}: {}", player->GetName(), body);
    }

    // "<verb>\t<body>" whole when it fits, else "<verb>\t#\t<seq>\t<count>\t<chunk>" pieces.
    void SendLong(Player* player, std::string const& verb, std::string const& body)
    {
        if (verb.size() + 1 + body.size() <= MAX_BODY)
        {
            Send(player, verb + "\t" + body);
            return;
        }
        size_t const room = MAX_BODY - verb.size() - 16;
        std::vector<std::string> chunks;
        for (size_t i = 0; i < body.size(); i += room)
            chunks.push_back(body.substr(i, room));
        for (size_t i = 0; i < chunks.size(); ++i)
            Send(player, verb + "\t#\t" + std::to_string(i + 1) + "\t" + std::to_string(chunks.size()) + "\t" + chunks[i]);
    }

    std::vector<std::string> Split(std::string_view s, char sep = '\t')
    {
        std::vector<std::string> out;
        size_t start = 0;
        while (true)
        {
            size_t p = s.find(sep, start);
            if (p == std::string_view::npos)
            {
                out.emplace_back(s.substr(start));
                break;
            }
            out.emplace_back(s.substr(start, p - start));
            start = p + 1;
        }
        return out;
    }

    bool IsOurs(uint32 type, uint32 lang, std::string const& msg)
    {
        return lang == LANG_ADDON && type == CHAT_MSG_WHISPER && msg.compare(0, CMD.size(), CMD) == 0;
    }

    ClassRow const* ClassOf(PlayerState const& st)
    {
        auto it = classes.find(st.classByte);
        return it == classes.end() ? nullptr : &it->second;
    }

    Budget BudgetFor(PlayerState const& st, uint8 level)
    {
        auto fam = essence.find(st.classByte);
        if (fam == essence.end() || fam->second.empty())
            return Budget{};
        auto it = fam->second.lower_bound(level);
        if (it == fam->second.end() || it->first != level)
        {
            if (it == fam->second.begin())
                return it->second;
            --it;
        }
        return it->second;
    }

    Spec const* ActiveSpec(PlayerState const& st)
    {
        auto it = specs.find(st.specId);
        return it == specs.end() ? nullptr : &it->second;
    }

    bool InActiveSpec(PlayerState const& st, Entry const& e)
    {
        Spec const* s = ActiveSpec(st);
        return s && e.tabUpper == s->specToken;
    }

    std::string EncodeKnown(PlayerState const& st)
    {
        std::string out;
        for (auto const& [entry, rank] : st.known)
        {
            if (!rank)
                continue;
            if (!out.empty())
                out += ',';
            out += std::to_string(entry) + ":" + std::to_string(rank);
        }
        return out;
    }

    void SendHello(Player* player)
    {
        Send(player, "HELLO\t" + std::to_string(PROTOCOL) + "\t" + g.mode + "\t" + (g.enabled && dataLoaded ? "1" : "0"));
    }

    // STATE\t<classByte>\t<specId>\t<ae>\t<te>\t<level>\t<chosen>\t<entry:rank,...>
    void SendState(Player* player, PlayerState const& st)
    {
        Budget b = BudgetFor(st, player->GetLevel());
        std::string body = std::to_string(st.classByte) + "\t" + std::to_string(st.specId) + "\t" + std::to_string(b.ae) + "\t" + std::to_string(b.te)
            + "\t" + std::to_string(player->GetLevel()) + "\t" + (st.chosen ? "1" : "0") + "\t" + EncodeKnown(st);
        SendLong(player, "STATE", body);
    }

    // The power bar the client draws comes from the stock class it was created with, so a
    // CoA class may only ride a carrier with the same bar (3.3.5 hunters run on mana).
    uint8 PowerOfStockClass(uint8 stockClass)
    {
        switch (stockClass)
        {
            case 1: return 1;   // warrior: rage
            case 4: return 3;   // rogue: energy
            case 6: return 6;   // death knight: runic power
            default: return 0;  // everyone else: mana
        }
    }
    bool CarrierFits(ClassRow const& cls, uint8 stockClass)
    {
        uint8 want = cls.powerType == 2 ? 0 : cls.powerType; // focus classes ride a mana carrier
        return PowerOfStockClass(stockClass) == want;
    }

    // ---- spells ------------------------------------------------------------------------
    // A spell shows in the spellbook only under a skill-line tab the character has;
    // Ascension's SkillLineAbility puts CA spells on class skill lines (99 Demolition,
    // 100 Invention, 102 Mechanics ... for Tinker) that a carrier-class character lacks.
    void EnsureSkillLine(Player* player, uint32 spell)
    {
        auto bounds = sSpellMgr->GetSkillLineAbilityMapBounds(spell);
        for (auto it = bounds.first; it != bounds.second; ++it)
        {
            uint32 skill = it->second->SkillLine;
            if (skill && !player->HasSkill(skill))
                player->SetSkill(static_cast<uint16>(skill), 0, 1, 1);
        }
    }

    void Learn(Player* player, uint32 spell)
    {
        EnsureSkillLine(player, spell);
        if (!player->HasSpell(spell))
            player->learnSpell(spell, false);
    }

    void ApplyRank(Player* player, Entry const& e, uint8 oldRank, uint8 newRank)
    {
        for (size_t i = 0; i < e.spells.size(); ++i)
        {
            uint32 spell = e.spells[i];
            if (!spell)
                continue;
            bool want = i < newRank;
            if (want)
                Learn(player, spell);
            else if (player->HasSpell(spell))
                player->removeSpell(spell, SPEC_MASK_ALL, false);
        }
        if (g.debug)
            LOG_INFO("module", "ASC {}: entry {} ({}) rank {} -> {}", player->GetName(), e.id, e.name, oldRank, newRank);
    }

    void EnsureSpells(Player* player, PlayerState const& st)
    {
        for (auto const& [id, rank] : st.known)
        {
            auto it = entries.find(id);
            if (it == entries.end())
                continue;
            for (size_t i = 0; i < it->second.spells.size() && i < rank; ++i)
                if (it->second.spells[i])
                    Learn(player, it->second.spells[i]);
        }
        if (Spec const* s = ActiveSpec(st))
            if (s->passiveSpell)
                Learn(player, s->passiveSpell);
    }

    // ---- persistence -------------------------------------------------------------------
    void SaveKnown(Player* player, PlayerState const& st)
    {
        ObjectGuid::LowType guid = player->GetGUID().GetCounter();
        CharacterDatabaseTransaction trans = CharacterDatabase.BeginTransaction();
        trans->Append("DELETE FROM ascension_ca_known WHERE guid = {}", guid);
        for (auto const& [entry, rank] : st.known)
            if (rank)
                trans->Append("INSERT INTO ascension_ca_known (guid, entry, `rank`) VALUES ({}, {}, {})", guid, entry, uint32(rank));
        trans->Append("REPLACE INTO ascension_ca_character (guid, class_byte, spec_id, chosen, archetype, updated_at) VALUES ({}, {}, {}, {}, '{}', {})",
                      guid, uint32(st.classByte), st.specId, uint32(st.chosen ? 1 : 0), st.archetype, uint32(GameTime::GetGameTime().count()));
        CharacterDatabase.CommitTransaction(trans);
    }

    PlayerState& LoadPlayer(Player* player)
    {
        ObjectGuid::LowType guid = player->GetGUID().GetCounter();
        PlayerState& st = players[guid];
        st.known.clear();
        st.classByte = g.defaultClassByte;
        st.specId = 0;
        st.chosen = false;
        st.archetype.clear();
        if (QueryResult r = CharacterDatabase.Query("SELECT class_byte, spec_id, chosen, archetype FROM ascension_ca_character WHERE guid = {}", guid))
        {
            Field* f = r->Fetch();
            st.classByte = f[0].Get<uint8>();
            st.specId = f[1].Get<uint32>();
            st.chosen = f[2].Get<uint8>() != 0;
            st.archetype = f[3].Get<std::string>();
        }
        else
            CharacterDatabase.Execute("INSERT INTO ascension_ca_character (guid, class_byte, spec_id, chosen, archetype, updated_at) VALUES ({}, {}, 0, 0, '', {})",
                                      guid, uint32(st.classByte), uint32(GameTime::GetGameTime().count()));
        if (classes.find(st.classByte) == classes.end())
            st.classByte = g.defaultClassByte;
        if (QueryResult r = CharacterDatabase.Query("SELECT entry, `rank` FROM ascension_ca_known WHERE guid = {}", guid))
        {
            do
            {
                Field* f = r->Fetch();
                uint32 id = f[0].Get<uint32>();
                if (entries.count(id))
                    st.known[id] = f[1].Get<uint8>();
            } while (r->NextRow());
        }
        return st;
    }

    // ---- validation ----------------------------------------------------------------------
    struct Verdict { bool ok = true; std::string reason; uint32 entry = 0; };

    Verdict Validate(Player* player, PlayerState const& st, std::map<uint32, uint8> const& wanted)
    {
        ClassRow const* cls = ClassOf(st);
        if (!cls)
            return { false, "no-class", 0 };
        Budget b = BudgetFor(st, player->GetLevel());
        uint32 ae = 0, te = 0;
        std::map<std::string, uint32> perTab;
        uint32 classPoints = 0;
        for (auto const& [id, rank] : wanted)
        {
            auto it = entries.find(id);
            if (it == entries.end())
                return { false, "unknown-entry", id };
            Entry const& e = it->second;
            if (e.classType != cls->caType)
                return { false, "other-class", id };
            if (rank < 1 || rank > e.maxRank)
                return { false, "max-rank", id };
            if (!e.classTab && !InActiveSpec(st, e))
                return { false, "other-specialization", id };
            if (e.reqLevel > player->GetLevel())
                return { false, "level", id };
            for (uint32 req : e.required)
            {
                auto r = wanted.find(req);
                if (r == wanted.end() || r->second < 1)
                    return { false, "prerequisite", id };
            }
            if (e.classTab) ae += rank; else te += rank;
            perTab[e.tabUpper] += rank;
            classPoints += rank;
        }
        if (ae > b.ae)
            return { false, "no-class-points", 0 };
        if (te > b.te)
            return { false, "no-spec-points", 0 };
        for (auto const& [id, rank] : wanted)
        {
            Entry const& e = entries.at(id);
            uint32 tabOthers = perTab[e.tabUpper] - rank;
            uint32 classOthers = classPoints - rank;
            if ((e.reqTabAe && tabOthers < e.reqTabAe) || (e.reqTabTe && tabOthers < e.reqTabTe)
                || (e.reqClassAe && classOthers < e.reqClassAe) || (e.reqClassTe && classOthers < e.reqClassTe)
                || (e.reqClassPoints && classOthers < e.reqClassPoints))
                return { false, "investment", id };
        }
        return {};
    }

    void Commit(Player* player, PlayerState& st, std::map<uint32, uint8> const& wanted)
    {
        // removals and rank drops first, then additions
        for (auto const& [id, oldRank] : st.known)
        {
            auto w = wanted.find(id);
            uint8 newRank = w == wanted.end() ? 0 : w->second;
            if (newRank < oldRank)
                ApplyRank(player, entries.at(id), oldRank, newRank);
        }
        for (auto const& [id, newRank] : wanted)
        {
            auto k = st.known.find(id);
            uint8 oldRank = k == st.known.end() ? 0 : k->second;
            if (newRank > oldRank)
                ApplyRank(player, entries.at(id), oldRank, newRank);
        }
        st.known.clear();
        for (auto const& [id, rank] : wanted)
            if (rank)
                st.known[id] = rank;
        SaveKnown(player, st);
    }

    std::map<uint32, uint8> ParseSet(std::string const& list)
    {
        std::map<uint32, uint8> out;
        for (std::string const& item : Split(list, ','))
        {
            size_t colon = item.find(':');
            if (item.empty() || colon == std::string::npos)
                continue;
            uint32 id = static_cast<uint32>(std::strtoul(item.substr(0, colon).c_str(), nullptr, 10));
            uint32 rank = static_cast<uint32>(std::strtoul(item.substr(colon + 1).c_str(), nullptr, 10));
            if (id && rank)
                out[id] = static_cast<uint8>(std::min<uint32>(rank, 255));
        }
        return out;
    }

    // ---- verbs -------------------------------------------------------------------------
    void HandleApply(Player* player, PlayerState& st, std::string const& list)
    {
        std::map<uint32, uint8> wanted = ParseSet(list);
        Verdict v = Validate(player, st, wanted);
        if (!v.ok)
        {
            Send(player, "RESULT\tAPPLY\tERR\t" + v.reason + "\t" + std::to_string(v.entry));
            SendState(player, st);
            return;
        }
        Commit(player, st, wanted);
        Send(player, "RESULT\tAPPLY\tOK");
        SendState(player, st);
    }

    void HandleSpec(Player* player, PlayerState& st, uint32 specId)
    {
        ClassRow const* cls = ClassOf(st);
        auto it = specs.find(specId);
        if (!cls || it == specs.end() || it->second.classToken != cls->token)
        {
            Send(player, "RESULT\tSPEC\tERR\tinvalid-spec\t" + std::to_string(specId));
            return;
        }
        if (st.specId == specId)
        {
            Send(player, "RESULT\tSPEC\tOK");
            SendState(player, st);
            return;
        }
        // the previous specialization's tree is unlearned: its tab no longer spends TE
        std::map<uint32, uint8> keep;
        for (auto const& [id, rank] : st.known)
        {
            Entry const& e = entries.at(id);
            if (e.classTab)
                keep[id] = rank;
        }
        if (Spec const* old = ActiveSpec(st))
            if (old->passiveSpell && player->HasSpell(old->passiveSpell))
                player->removeSpell(old->passiveSpell, SPEC_MASK_ALL, false);
        Commit(player, st, keep);
        st.specId = specId;
        if (it->second.passiveSpell)
            Learn(player, it->second.passiveSpell);
        SaveKnown(player, st);
        Send(player, "RESULT\tSPEC\tOK");
        SendState(player, st);
    }

    // Learn the next entries of the character's archetype build that fit the level and the
    // budget, in build order; called after CLASS and after every level-up.
    void ApplyArchetype(Player* player, PlayerState& st)
    {
        if (st.archetype.empty())
            return;
        auto it = archetypes.find(st.archetype);
        if (it == archetypes.end())
            return;
        std::map<uint32, uint8> wanted = st.known;
        bool changed = false;
        for (auto const& [entry, rank] : it->second.entries)
        {
            auto k = wanted.find(entry);
            if (k != wanted.end() && k->second >= rank)
                continue;
            std::map<uint32, uint8> trial = wanted;
            trial[entry] = rank;
            if (Validate(player, st, trial).ok)
            {
                wanted = trial;
                changed = true;
            }
        }
        if (changed)
            Commit(player, st, wanted);
    }

    // CLASS\t<classByte>[\t<archetypeUUID>]: the glue chooser's pick, delivered by the addon
    // pack on the character's first world entry. Accepted once, while the character has no
    // Character Advancement rows yet.
    void HandleClass(Player* player, PlayerState& st, std::vector<std::string> const& args)
    {
        uint32 byte = args.size() > 1 ? static_cast<uint32>(std::strtoul(args[1].c_str(), nullptr, 10)) : 0;
        std::string uuid = args.size() > 2 ? args[2] : "";
        auto cls = classes.find(static_cast<uint8>(byte));
        if (byte == 0 || byte > 255 || cls == classes.end())
        {
            Send(player, "RESULT\tCLASS\tERR\tunknown-class\t" + std::to_string(byte));
            return;
        }
        if (st.chosen || !st.known.empty())
        {
            Send(player, "RESULT\tCLASS\tERR\talready-chosen\t" + std::to_string(st.classByte));
            SendState(player, st);
            return;
        }
        if (!CarrierFits(cls->second, player->getClass()))
        {
            Send(player, "RESULT\tCLASS\tERR\tcarrier-mismatch\t" + std::to_string(player->getClass()));
            return;
        }
        for (char c : uuid)
            if (!(std::isxdigit(static_cast<unsigned char>(c)) || c == '-'))
            {
                Send(player, "RESULT\tCLASS\tERR\tbad-archetype\t0");
                return;
            }
        if (!uuid.empty())
        {
            auto a = archetypes.find(uuid);
            if (a == archetypes.end() || (a->second.classType && a->second.classType != cls->second.caType))
            {
                Send(player, "RESULT\tCLASS\tERR\tbad-archetype\t0");
                return;
            }
        }
        st.classByte = static_cast<uint8>(byte);
        st.chosen = true;
        st.archetype = uuid;
        st.specId = 0;
        SaveKnown(player, st);
        ApplyArchetype(player, st);
        LOG_INFO("module", "ASC {}: CoA class {} ({}) chosen{}", player->GetName(), byte, cls->second.name, uuid.empty() ? "" : " with archetype " + uuid);
        Send(player, "RESULT\tCLASS\tOK");
        SendState(player, st);
    }

    void HandleReset(Player* player, PlayerState& st, std::string const& what)
    {
        std::map<uint32, uint8> keep;
        if (what != "all")
            for (auto const& [id, rank] : st.known)
                if (entries.at(id).classTab)
                    keep[id] = rank;
        Commit(player, st, keep);
        Send(player, "RESULT\tRESET\tOK");
        SendState(player, st);
    }

    // args[0] = verb, args[1..] = arguments (already reassembled when fragmented).
    void Handle(Player* player, std::vector<std::string> const& args)
    {
        if (args.empty())
            return;
        std::string const& verb = args[0];
        if (g.debug)
            LOG_INFO("module", "ASC <- {}: {}", player->GetName(), verb);
        if (verb == "PING") { Send(player, "PONG"); return; }
        if (verb == "HELLO") { SendHello(player); return; }
        if (verb == "LOG")
        {
            std::string line;
            for (size_t i = 1; i < args.size(); ++i)
                line += (i > 1 ? "\t" : "") + args[i];
            LOG_INFO("module", "ASC LOG [{}] {}", player->GetName(), line);
            return;
        }
        if (!g.enabled || !dataLoaded)
        {
            Send(player, "ERROR\tdisabled");
            return;
        }
        auto ps = players.find(player->GetGUID().GetCounter());
        PlayerState& st = ps == players.end() ? LoadPlayer(player) : ps->second;
        if (verb == "STATE") { SendState(player, st); return; }
        if (verb == "APPLY") { HandleApply(player, st, args.size() > 1 ? args[1] : ""); return; }
        if (verb == "SPEC") { HandleSpec(player, st, args.size() > 1 ? static_cast<uint32>(std::strtoul(args[1].c_str(), nullptr, 10)) : 0); return; }
        if (verb == "RESET") { HandleReset(player, st, args.size() > 1 ? args[1] : "talents"); return; }
        if (verb == "CLASS") { HandleClass(player, st, args); return; }
        Send(player, "ERROR\tunknown\t" + verb);
    }

    // Fragment bookkeeping for client -> server: CMD\t<verb>\t#\t<seq>\t<count>\t<chunk>
    void Receive(Player* player, std::string_view payload)
    {
        auto fields = Split(payload);
        if (fields.size() >= 5 && fields[1] == "#")
        {
            auto ps = players.find(player->GetGUID().GetCounter());
            PlayerState& st = ps == players.end() ? LoadPlayer(player) : ps->second;
            std::string const& verb = fields[0];
            uint32 seq = static_cast<uint32>(std::strtoul(fields[2].c_str(), nullptr, 10));
            uint32 count = static_cast<uint32>(std::strtoul(fields[3].c_str(), nullptr, 10));
            std::string chunk;
            for (size_t i = 4; i < fields.size(); ++i)
                chunk += (i > 4 ? "\t" : "") + fields[i];
            if (!seq || !count || count > 64)
                return;
            st.inbound[verb][seq] = chunk;
            st.inboundCount[verb] = count;
            if (st.inbound[verb].size() < count)
                return;
            std::string body;
            for (uint32 i = 1; i <= count; ++i)
                body += st.inbound[verb][i];
            st.inbound.erase(verb);
            st.inboundCount.erase(verb);
            Handle(player, { verb, body });
            return;
        }
        Handle(player, fields);
    }
}

class AscensionCA_PlayerScript : public PlayerScript
{
public:
    AscensionCA_PlayerScript() : PlayerScript("AscensionCA_PlayerScript", {
        PLAYERHOOK_ON_LOGIN,
        PLAYERHOOK_ON_LOGOUT,
        PLAYERHOOK_ON_LEVEL_CHANGED,
        PLAYERHOOK_ON_DELETE_FROM_DB,
        PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE,
        PLAYERHOOK_CAN_PLAYER_USE_PRIVATE_CHAT
    }) { }

    void OnPlayerLogin(Player* player) override
    {
        if (AscensionCA::g.enabled && AscensionCA::dataLoaded)
        {
            AscensionCA::PlayerState& st = AscensionCA::LoadPlayer(player);
            AscensionCA::EnsureSpells(player, st);
        }
        AscensionCA::SendHello(player);
    }

    void OnPlayerLogout(Player* player) override
    {
        AscensionCA::players.erase(player->GetGUID().GetCounter());
    }

    void OnPlayerLevelChanged(Player* player, uint8 /*oldLevel*/) override
    {
        auto it = AscensionCA::players.find(player->GetGUID().GetCounter());
        if (it != AscensionCA::players.end())
        {
            AscensionCA::ApplyArchetype(player, it->second);
            AscensionCA::SendState(player, it->second);
        }
    }

    void OnPlayerDeleteFromDB(CharacterDatabaseTransaction trans, uint32 guid) override
    {
        trans->Append("DELETE FROM ascension_ca_known WHERE guid = {}", guid);
        trans->Append("DELETE FROM ascension_ca_character WHERE guid = {}", guid);
    }

    void OnPlayerBeforeSendChatMessage(Player* player, uint32& type, uint32& lang, std::string& msg) override
    {
        if (!AscensionCA::IsOurs(type, lang, msg))
            return;
        AscensionCA::Receive(player, std::string_view(msg).substr(AscensionCA::CMD.size()));
    }

    bool OnPlayerCanUseChat(Player* /*player*/, uint32 type, uint32 lang, std::string& msg, Player* /*receiver*/) override
    {
        // Our control lines must not be relayed (they would echo back as CHAT_MSG_ADDON).
        return !AscensionCA::IsOurs(type, lang, msg);
    }
};

class AscensionCA_WorldScript : public WorldScript
{
public:
    AscensionCA_WorldScript() : WorldScript("AscensionCA_WorldScript", { WORLDHOOK_ON_AFTER_CONFIG_LOAD, WORLDHOOK_ON_STARTUP }) { }

    void OnAfterConfigLoad(bool /*reload*/) override
    {
        AscensionCA::Load();
    }

    void OnStartup() override
    {
        AscensionCA::LoadData();
    }
};

void AddSC_AscensionCA()
{
    new AscensionCA_PlayerScript();
    new AscensionCA_WorldScript();
}
