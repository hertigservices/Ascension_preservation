-- GameTooltipExtras.lua: the GameTooltip_* helpers Ascension added to FrameXML\GameTooltip.lua
-- (Interface\FrameXML\GameTooltip.lua in patch-B, lines 1144-1299) that a stock 3.3.5a client
-- lacks. Copied verbatim; only the ones the Character Advancement / Collections cluster calls
-- (GameTooltip_AddSpacer, GameTooltip_GenericTooltip, GameTooltip_AddIDLine and their helpers).
-- The rest of Ascension's GameTooltip.lua (OnSetItem/OnSetSpell rewrites, lockouts, item
-- version replacement) stays out until the tooltip phase (P7).
ASCENSION_PRIMARY_COLOR = ASCENSION_PRIMARY_COLOR or (CreateColor and CreateColor(1.0, 0.55, 0.0)) or NORMAL_FONT_COLOR
ASCENSION_SECONDARY_COLOR = ASCENSION_SECONDARY_COLOR or (CreateColor and CreateColor(1.0, 1.0, 1.0)) or HIGHLIGHT_FONT_COLOR

if not GameTooltip_AddSpacer then
function GameTooltip_AddSpacer(self)
	self:AddLine(" ")
end
end

if not GameTooltip_AutoAnchor then
function GameTooltip_AutoAnchor(self)
	local x, y = self:GetCenter()
	local midY = GetScreenHeight() / 2
	local midX = GetScreenWidth() / 2

	local xPos = x >= midX and "LEFT" or "RIGHT"
	local yPos = y >= midY and "BOTTOM" or ""

	return "ANCHOR_"..yPos..xPos
end
end

if not GameTooltip_GenericTooltip then
function GameTooltip_GenericTooltip(frame, anchor)
	local title = frame.tooltipTitle or frame.tooltip
	if title then
		if not anchor then anchor = GameTooltip_AutoAnchor(frame) end
		GameTooltip:SetOwner(frame, anchor)
		GameTooltip:SetText(title, 1, 1, 1, 1)
		local tooltipText = frame.tooltipText or frame.tooltip2
		if tooltipText then
			if type(tooltipText) == "table" then
				for _, line in ipairs(tooltipText) do
					local text, r, g, b
					if type(line) == "table" then
						text, r, g, b = unpack(line)
					else
						text = line
						r, g, b = NORMAL_FONT_COLOR:GetRGB()
					end
					GameTooltip:AddLine(text, r, g, b, true)
				end
			else
				GameTooltip:AddLine(tooltipText, NORMAL_FONT_COLOR.r, NORMAL_FONT_COLOR.g, NORMAL_FONT_COLOR.b, true)
			end
		end
		GameTooltip:Show()
	end
end
end

if not GameTooltip_AddIDLine then
function GameTooltip_AddIDLine(self, label, id)
	self:AddLine(ASCENSION_PRIMARY_COLOR:WrapText(label) .." ".. ASCENSION_SECONDARY_COLOR:WrapText(id), 1, 1, 1, true)
end
end

if not GameTooltip_AddDoubleIDLine then
function GameTooltip_AddDoubleIDLine(self, label, id, label2, id2)
	local line = ASCENSION_PRIMARY_COLOR:WrapText(label) .." ".. ASCENSION_SECONDARY_COLOR:WrapText(id)
	local line2 = ASCENSION_PRIMARY_COLOR:WrapText(label2) .." ".. ASCENSION_SECONDARY_COLOR:WrapText(id2)
	self:AddDoubleLine(line, line2, 1, 1, 1, 1, 1, 1)
end
end
