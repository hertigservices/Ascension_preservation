-- AscensionUIExtras.lua: the handful of globals Ascension's always-loaded AscensionUI addon
-- provides that the Collections panels call. AscensionUI itself (809 KB, a whole UI overhaul) is
-- not part of the pack; these are the only entry points the panels reach.
--   BaseFrameFadeIn / BaseFrameFadeOut  (AscensionUI.lua:51-70)  -- alpha fades; stock UIFrameFade does the same job
--   MagicButton_OnLoad                  (Shared/ButtonTemplates.lua) -- separator/anchor cosmetics on CurrencyBar buttons
--   IconSelectCreateFrame               (Shared/IconSelector.lua)    -- copied verbatim by the assembler, not restated here
if not BaseFrameFadeIn then
	function BaseFrameFadeIn(frame)
		if not frame then return end
		frame.ForceLeaveFade = false
		local duration = frame.time or 0.5
		if UIFrameFadeIn then UIFrameFadeIn(frame, duration, frame:GetAlpha(), 1) end
		frame:Show()
	end
end
if not BaseFrameFadeOut then
	function BaseFrameFadeOut(frame)
		if not frame then return end
		frame.ForceLeaveFade = false
		local duration = frame.time or 0.5
		if UIFrameFadeOut then
			local info = { mode = "OUT", timeToFade = duration, startAlpha = frame:GetAlpha(), endAlpha = 0, finishedFunc = function() frame:Hide() end }
			UIFrameFade(frame, info)
		else
			frame:Hide()
		end
	end
end
if not MagicButton_OnLoad then
	function MagicButton_OnLoad(self) end
end
