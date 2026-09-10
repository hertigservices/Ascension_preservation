-- Offline catalogue adapter for original CoA/Hero Architect read calls.
-- Source contracts: CoATalentFrame.lua:258-300, BuildListItem.lua, BuildCreator.lua:727-752.
-- This never activates a build, teaches a spell, posts a rating or invents player history.
local API=ASC.Namespace("C_BuildCreator")
local Builds=ASC.Builds
local reason="ASC_PREVIEW_READ_ONLY"
local results,notifications={},{}
local category
local function notify(event,...)
    notifications[#notifications+1]={event=event,args={...},n=select("#",...)}
end
local function categoryMatches(value)
    return not category or category=="None" or value==category or (category=="Level60PvPvE" and (value=="Level60PvE" or value=="Level60PvP"))
end
-- The panel's filter keys (Enum.BuildFilter), applied to what the captured records carry.
-- Groups are AND-ed, members of a group OR-ed, like the live panel. The catalogue is a capture
-- of ONE realm's community builds (metadata.realm); every record is a build for that realm's
-- classless Hero, so FILTER_CLASS_HERO passes all and any CoA class filter passes none -
-- the records carry no class attribution to do better with. Featured has no flag in the
-- capture and passes all; Owned can never match (nothing here is the player's).
local function passesFilters(record,filters,catalogue)
    if type(filters)~="table" then return true end
    local groups={}
    local wantOwned=false
    for key,enabled in pairs(filters) do
        key=type(key)=="string" and key or (type(enabled)=="string" and enabled or nil)
        if key and enabled~=false then
            if key=="FILTER_OWNED_BUILDS" then wantOwned=true
            elseif key=="FILTER_FEATURED" then -- no curation flag was captured
            elseif key=="FILTER_LAST_30_DAYS" then
                groups.recent=groups.recent or {}
                groups.recent[#groups.recent+1]=true
            elseif string.find(key,"^FILTER_CLASS_") then
                groups.class=groups.class or {}
                groups.class[#groups.class+1]=string.sub(key,14)
            elseif string.find(key,"^FILTER_DIFFICULTY_RATING_") then
                groups.difficulty=groups.difficulty or {}
                groups.difficulty[#groups.difficulty+1]="BUILD_DIFFICULTY_RATING_"..string.sub(key,26)
            elseif string.find(key,"^FILTER_PRIMARY_STAT_") then
                groups.stat=groups.stat or {}
                groups.stat[#groups.stat+1]="STAT_"..string.sub(key,21)
            elseif string.find(key,"^FILTER_ROLE_") then
                groups.role=groups.role or {}
                groups.role[#groups.role+1]="PLAYER_ROLE_"..string.sub(key,13)
            end
        end
    end
    if wantOwned then return false end
    local function anyOf(list,value)
        for _,v in ipairs(list) do if v==value then return true end end
        return false
    end
    if groups.class and not anyOf(groups.class,"HERO") then return false end
    if groups.difficulty and not anyOf(groups.difficulty,record.DifficultyRating) then return false end
    if groups.stat and not anyOf(groups.stat,record.PrimaryStat) then return false end
    if groups.role and not anyOf(groups.role,record.Roles) then return false end
    if groups.recent then
        local y,m,d=string.match(tostring(catalogue.metadata.capturedDate or ""),"^(%d+)-(%d+)-(%d+)$")
        local captured=y and time({year=tonumber(y),month=tonumber(m),day=tonumber(d),hour=0})
        local stamp=record.UpdatedTime or record.CreatedTime
        if not (captured and stamp and stamp>=captured-30*86400) then return false end
    end
    return true
end
function API.UpdateFilter(text,filters,sort)
    results={}
    local catalogue=Builds.Current()
    if not catalogue then return false,"catalogue-not-selected" end
    text=string.lower(type(text)=="string" and text or "")
    local sortKey
    for key,enabled in pairs(sort or {}) do
        if enabled then
            if sortKey or not (key=="SORT_NONE" or key=="SORT_RATING_ASCENDING" or key=="SORT_RATING_DESCENDING" or key=="SORT_DATE_ASCENDING" or key=="SORT_DATE_DESCENDING") then
                return false,"unsupported-preview-build-sort"
            end
            sortKey=key
        end
    end
    for _,id in ipairs(catalogue.ordered) do
        local record=catalogue.records[id]
        local haystack=string.lower((record.Name or "").."\n"..(record.AuthorName or "").."\n"..(record.Description or "").."\n"..(record.Subtext or ""))
        if categoryMatches(record.Category) and (text=="" or string.find(haystack,text,1,true)) and passesFilters(record,filters,catalogue) then results[#results+1]=id end
    end
    table.sort(results,function(a,b)
        local left,right=catalogue.records[a],catalogue.records[b]
        local field,descending
        if sortKey=="SORT_RATING_ASCENDING" or sortKey=="SORT_RATING_DESCENDING" then field="Upvotes";descending=sortKey=="SORT_RATING_DESCENDING"
        elseif sortKey=="SORT_DATE_ASCENDING" or sortKey=="SORT_DATE_DESCENDING" then field="UpdatedTime";descending=sortKey=="SORT_DATE_DESCENDING" end
        if field and left[field]~=right[field] then
            if left[field]==nil then return false end
            if right[field]==nil then return true end
            if descending then return left[field]>right[field] end
            return left[field]<right[field]
        end
        local ln,rn=string.lower(left.Name),string.lower(right.Name)
        if ln~=rn then return ln<rn end
        return a<b
    end)
    return true
end
function API.QueryAllBuilds(value)
    if value~=nil and type(value)~="string" then return false,"invalid-preview-category" end
    category=value
    local ok,err=API.UpdateFilter("",{}, {})
    -- Deferred: original BuildCreator calls ShowLoading AFTER QueryAllBuilds.
    notify("BUILD_CREATOR_CATEGORY_RESULT",value)
    return ok,err
end
function API.GetNumBuilds() return #results end
function API.GetBuild(id)
    local catalogue=Builds.Current()
    return catalogue and Builds.Copy(catalogue.records[id]) or nil
end
function API.GetBuildAtIndex(index)
    return API.GetBuild(results[index])
end
function API.GetSpell(id,spellID)
    local record=API.GetBuild(id)
    if record then for _,spell in ipairs(record.Spells) do if spell.Spell==spellID then return spell end end end
end
function API.IsDetailedBuild(id)
    local catalogue=Builds.Current()
    if catalogue then return catalogue.detailed[id] end
    return nil
end
function API.IsUpvotedBuild() return false end -- new isolated preview has no votes
function API.IsOwnedBuild() return false end
function API.GetActiveBuild() return nil end
function API.GetNumBookmarkedBuilds() return 0 end
function API.GetBookmarkedBuildAtIndex() return nil end
function API.CanActivateBuild() return false,{reason} end
local function deny() return false,reason end
API.RateBuild=deny
API.BookmarkBuild=deny
API.ActivateBuild=deny
API.DeactivateBuild=deny
function Builds.FlushNotifications()
    local pending=notifications;notifications={}
    for _,item in ipairs(pending) do ASC.Events.Fire(item.event,unpack(item.args,1,item.n)) end
end
