-- StaticPopupExtras.lua: the Character Advancement dialogs from Ascension's FrameXML\StaticPopup.lua
-- (which replaces the whole StaticPopupDialogs table and so cannot be copied wholesale onto a
-- stock client). Bodies are verbatim; only the dialogs the CA panels open are here.
StaticPopupDialogs = StaticPopupDialogs or {}

StaticPopupDialogs["CLOSE_CHARACTER_ADVANCEMENT_UNSAVED_PENDING_CHANGES"] = {
	text = CLOSE_CHARACTER_ADVANCEMENT_UNSAVED_PENDING_CHANGES,
	button1 = APPLY,
	button2 = DISCARD,
	button3 = GO_BACK,
	OnButton1 = function(self)
		CharacterAdvancementUtil.ConfirmApplyPendingBuild()
	end,
	OnButton2 = function(self)
		CharacterAdvancementUtil.MarkForSwap(nil)
		BuildCreatorUtil.ClearPendingBuildID()
		C_CharacterAdvancement.CancelPendingBuild()
	end,
	OnButton3 = function(self)
		Collections:GoToTab(Collections.Tabs.CharacterAdvancement)
	end,
	OnShow = function(self)
		self.button1:SetEnabled(C_CharacterAdvancement.CanApplyPendingBuild())
	end
}

StaticPopupDialogs["CONFIRM_RESET_BUILD_NO_COST"] = {
	text = CONFIRM_RESET_BUILD_NO_COST,
	button1 = UNLEARN,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function()
		C_CharacterAdvancement.ApplyPendingBuild()
	end,
	OnCancel = function()
		BuildCreatorUtil.ClearPendingBuildID()
		C_CharacterAdvancement.CancelPendingBuild()
	end
}

StaticPopupDialogs["CONFIRM_RESET_BUILD"] = {
	text = CONFIRM_RESET_BUILD,
	button1 = UNLEARN,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function()
		C_CharacterAdvancement.ApplyPendingBuild()
	end,
	OnCancel = function()
		BuildCreatorUtil.ClearPendingBuildID()
		C_CharacterAdvancement.CancelPendingBuild()
	end
}

StaticPopupDialogs["CONFIRM_APPLY_PENDING_BUILD_NO_COST"] = {
	text = CONFIRM_APPLY_PENDING_BUILD_NO_COST,
	button1 = APPLY,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function()
		C_CharacterAdvancement.ApplyPendingBuild()
		if BuildCreatorUtil.GetPendingBuildID() then
			C_BuildCreator.ActivateBuild(BuildCreatorUtil.GetPendingBuildID(), true, true)
			BuildCreatorUtil.ClearPendingBuildID()
		end
	end,
}

StaticPopupDialogs["CONFIRM_APPLY_PENDING_BUILD"] = {
	text = CONFIRM_APPLY_PENDING_BUILD,
	button1 = APPLY,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function()
		C_CharacterAdvancement.ApplyPendingBuild()
		if BuildCreatorUtil.GetPendingBuildID() then
			C_BuildCreator.ActivateBuild(BuildCreatorUtil.GetPendingBuildID(), true, true)
			BuildCreatorUtil.ClearPendingBuildID()
		end
	end,
}

StaticPopupDialogs["CONFIRM_UNLEARN_ALL_S"] = {
	text = CONFIRM_UNLEARN_ALL_S,
	button1 = UNLEARN,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function(self, unlearnFunc)
		unlearnFunc()
	end,
}

StaticPopupDialogs["CONFIRM_LEARN_S"] = {
	text = CONFIRM_LEARN_S,
	button1 = LEARN,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function(self, internalID)
		C_CharacterAdvancement.LearnID(internalID)
	end,
}

StaticPopupDialogs["CONFIRM_LEAVING_PENDING"] = {
	text = CONFIRM_LEAVING_PENDING,
	button1 = ACCEPT,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function(self, data)
		CharacterAdvancementUtil.MarkForSwap(data[1], data[2])
	end,
}

StaticPopupDialogs["CONFIRM_SWAP_S"] = {
	text = CONFIRM_SWAP_S,
	button1 = ACCEPT,
	hideOnEscape = true,
	OnAccept = function(self, internalID)
	end,
}

StaticPopupDialogs["CONFIRM_UNLEARN_S"] = {
	text = CONFIRM_UNLEARN_S,
	button1 = UNLEARN,
	button2 = CANCEL,
	hideOnEscape = true,
	OnAccept = function(self, internalID)
		C_CharacterAdvancement.UnlearnID(internalID)
	end,
}
