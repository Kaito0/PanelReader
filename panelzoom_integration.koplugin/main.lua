local Device = require("device")
local Dispatcher = require("dispatcher")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local InfoMessage = require("ui/widget/infomessage")
local Screen = require("device").screen
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local ImageViewer = require("ui/widget/imageviewer")
local _ = require("gettext")
local logger = require("logger")
local util = require("util")
local json = require("json")

local PanelZoomIntegration = WidgetContainer:extend{
    name = "panelzoom_integration",
    integration_mode = false,
    current_panels = {},
    current_panel_index = 1,
    last_page_seen = -1,
    tap_navigation_enabled = true,
    tap_zones = { left = 0.3, right = 0.7 },
}

-- Create a reusable PanelViewer class once
local PanelViewer = ImageViewer:extend{}

function PanelViewer:onTap(_, ges)
    if not ges or not ges.pos then return false end
    
    local x_pct = ges.pos.x / Screen:getWidth()
    -- Determine direction based on JSON if available, else default to LTR
    local is_rtl = self.panel_integration.reading_direction == "rtl"
    
    -- Zone Logic: In RTL, Left is "Forward". In LTR, Right is "Forward".
    local is_forward = (is_rtl and x_pct < 0.3) or (not is_rtl and x_pct > 0.7)
    local is_backward = (is_rtl and x_pct > 0.7) or (not is_rtl and x_pct < 0.3)

    if is_forward then
        logger.info("PanelZoom: Forward tap detected")
        if self.panel_integration.current_panel_index < #self.panel_integration.current_panels then
            self.panel_integration.current_panel_index = self.panel_integration.current_panel_index + 1
            self.panel_integration:displayCurrentPanel()
        else
            -- Last panel reached, jump to next page
            logger.info("PanelZoom: Last panel reached, jumping to next page")
            self.panel_integration:changePage(1) 
        end
        return true
    elseif is_backward then
        logger.info("PanelZoom: Backward tap detected")
        if self.panel_integration.current_panel_index > 1 then
            self.panel_integration.current_panel_index = self.panel_integration.current_panel_index - 1
            self.panel_integration:displayCurrentPanel()
        else
            -- First panel reached, jump to previous page
            logger.info("PanelZoom: First panel reached, jumping to previous page")
            self.panel_integration:changePage(-1)
        end
        return true
    end

    -- Center tap: Close the viewer
    logger.info("PanelZoom: Center tap detected, closing viewer")
    UIManager:close(self)
    return true
end

function PanelZoomIntegration:init()
    self:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)
end

function PanelZoomIntegration:handleEvent(ev)
    if ev.type == "TogglePanelZoomIntegration" then
        self:toggleIntegrationMode()
        return true
    end
    return false
end

function PanelZoomIntegration:onDispatcherRegisterActions()
    Dispatcher:registerAction("panelzoom_integration_action", {
        category="none", event="TogglePanelZoomIntegration",
        title=_("Toggle Panel Zoom Integration"), general=true,
    })
end

function PanelZoomIntegration:addToMainMenu(menu_items)
    menu_items.panelzoom_integration = {
        text = _("Panel Zoom Integration"),
        sorting_hint = "more_tools",
        sub_item_table = {
            {
                text = _("Enable Integration Mode"),
                checked_func = function() return self.integration_mode end,
                callback = function() self:toggleIntegrationMode() end,
            },
        },
    }
end

function PanelZoomIntegration:toggleIntegrationMode()
    self.integration_mode = not self.integration_mode
    if self.integration_mode then
        if self.ui.highlight then self.ui.highlight.panel_zoom_enabled = true end
        self:overridePanelZoom()
        -- Reset panel data when enabling
        self.current_panels = {}
        self.current_panel_index = 1
        self.last_page_seen = -1
    else
        self:restorePanelZoom()
    end
    UIManager:show(InfoMessage:new{ text = self.integration_mode and "Integration ON" or "Integration OFF", timeout = 1 })
end

function PanelZoomIntegration:overridePanelZoom()
    if not self.ui.highlight then return end
    self.ui.highlight.onPanelZoom = function(inst, arg, ges)
        return self:onIntegratedPanelZoom(arg, ges)
    end
end

function PanelZoomIntegration:changePage(diff)
    -- 1. Use KOReader's built-in page navigation method
    if self.ui.paging and self.ui.paging.onGotoViewRel then
        self.ui.paging:onGotoViewRel(diff)
        logger.info(string.format("PanelZoom: Used ui.paging.onGotoViewRel(%d)", diff))
    else
        -- Fallback to key event
        local key = diff > 0 and "Right" or "Left"
        UIManager:sendEvent({ key = key, modifiers = {} })
        logger.info(string.format("PanelZoom: Used %s key event as fallback", key))
    end
        
    -- 2. Wait for the engine to render the new page, then update viewer content
    UIManager:scheduleIn(0.3, function()
        local new_page = self:getSafePageNumber()
        logger.info(string.format("PanelZoom: Changed to page %d (diff: %d)", new_page, diff))
        self.last_page_seen = new_page
        self:importToggleZoomPanels()
        
        if #self.current_panels > 0 then
            -- If going forward, start at panel 1. If going backward, start at last panel.
            self.current_panel_index = diff > 0 and 1 or #self.current_panels
            -- Just update the current viewer instead of closing/reopening
            self:displayCurrentPanel()
        else
            -- No panels on this page, close viewer
            if self._current_imgviewer then
                UIManager:close(self._current_imgviewer)
                self._current_imgviewer = nil
            end
            UIManager:show(InfoMessage:new{ text = _("No panels on this page"), timeout = 1 })
        end
    end)
end

function PanelZoomIntegration:restorePanelZoom()
    if self.ui.highlight then self.ui.highlight.onPanelZoom = nil end
end

function PanelZoomIntegration:getSafePageNumber()
    -- Try multiple methods to get the current page number
    local page = nil
    
    -- Method 1: Try ui.paging.getPage()
    if self.ui.paging and self.ui.paging.getPage then 
        page = self.ui.paging:getPage()
        logger.info(string.format("PanelZoom: Method 1 - ui.paging.getPage() -> %d", page))
    end
    
    -- Method 2: Try ui.paging.cur_page
    if not page and self.ui.paging and self.ui.paging.cur_page then 
        page = self.ui.paging.cur_page
        logger.info(string.format("PanelZoom: Method 2 - ui.paging.cur_page -> %d", page))
    end
    
    -- Method 3: Try ui.document.current_page
    if not page and self.ui.document and self.ui.document.current_page then 
        page = self.ui.document.current_page
        logger.info(string.format("PanelZoom: Method 3 - ui.document.current_page -> %d", page))
    end
    
    -- Method 4: Try ui.view.state.page
    if not page and self.ui.view and self.ui.view.state and self.ui.view.state.page then 
        page = self.ui.view.state.page
        logger.info(string.format("PanelZoom: Method 4 - ui.view.state.page -> %d", page))
    end
    
    -- Method 5: Try getting from the highlighting system
    if not page and self.ui.highlight and self.ui.highlight.page then 
        page = self.ui.highlight.page
        logger.info(string.format("PanelZoom: Method 5 - ui.highlight.page -> %d", page))
    end
    
    -- Fallback
    if not page then 
        page = 1
        logger.info("PanelZoom: Using fallback page number 1")
    end
    
    return page
end

function PanelZoomIntegration:onIntegratedPanelZoom(arg, ges)
    -- Ensure we have the gesture object
    local actual_ges = (type(arg) == "table" and arg.pos) and arg or ges
    if not self.integration_mode then return false end

    local current_page = self:getSafePageNumber()
    logger.info(string.format("PanelZoom: onIntegratedPanelZoom called - current_page: %d, last_page_seen: %d, panels_count: %d", 
        current_page, self.last_page_seen or -1, #self.current_panels))
    
    -- Force import if page changed or panels empty
    if current_page ~= self.last_page_seen or #self.current_panels == 0 then
        logger.info(string.format("PanelZoom: Page changed or no panels - importing for page %d", current_page))
        self.last_page_seen = current_page
        self:importToggleZoomPanels()
    else
        logger.info(string.format("PanelZoom: Using cached panels for page %d", current_page))
    end

    if #self.current_panels > 0 then
        self.current_panel_index = 1
        return self:displayCurrentPanel()
    end

    logger.warn("PanelZoom: No panels found for this page in JSON.")
    return false
end

function PanelZoomIntegration:importToggleZoomPanels()
    local doc_path = self.ui.document.file
    if not doc_path then return end
    
    local dir, filename = util.splitFilePathName(doc_path)
    local base_name = filename:match("(.+)%..+$") or filename
    local json_path = dir .. "/" .. base_name .. ".json"
    
    local f = io.open(json_path, "r")
    if not f then 
        logger.warn("PanelZoom: JSON not found at " .. json_path)
        return 
    end
    
    local content = f:read("*all")
    f:close()
    
    local ok, data = pcall(json.decode, content)
    if not ok or not data then return end

    -- Save the reading direction for the Tap handler
    self.reading_direction = data.reading_direction or "ltr"
    logger.info(string.format("PanelZoom: Reading direction set to %s", self.reading_direction))

    local page_idx = self:getSafePageNumber()
    local panels = nil

    -- Handle array-based JSON structure: pages is an array of objects
    if data.pages and type(data.pages) == "table" and #data.pages > 0 then
        -- Iterate through the pages array to find matching page number
        for _, page_data in ipairs(data.pages) do
            if page_data.page == page_idx then
                panels = page_data.panels
                logger.info(string.format("PanelZoom: Found page %d in array structure", page_idx))
                break
            end
        end
    end
    
    -- Fallback: Try dictionary-style access (for backward compatibility)
    if not panels and data.pages then
        -- 1. Try filename (e.g. "page001.jpg")
        -- 2. Try page index as string ("1")
        -- 3. Try page index as number (1)
        panels = data.pages[filename] or data.pages[tostring(page_idx)] or data.pages[page_idx]
    end
    
    -- Final fallback: if JSON has a top-level 'panels' array
    if not panels and data.panels then panels = data.panels end

    if panels and #panels > 0 then
        self.current_panels = panels
        logger.info(string.format("PanelZoom: SUCCESS! Loaded %d panels for page %d", #panels, page_idx))
    else
        self.current_panels = {}
        logger.warn(string.format("PanelZoom: JSON found, but no panels match page %d or filename %s", page_idx, filename))
    end
end

function PanelZoomIntegration:displayCurrentPanel()
    logger.info("PanelZoom: displayCurrentPanel called")
    local panel = self.current_panels[self.current_panel_index]
    if not panel then 
        logger.warn("PanelZoom: No panel data found for index " .. self.current_panel_index)
        return false 
    end

    local page = self:getSafePageNumber()
    
    -- Get dimensions from the View object for perfect alignment
    local view = self.ui.view
    local doc_w, doc_h
    
    if view and view.page_visible and view.page_visible.area then
        -- Use the actual view area dimensions
        local view_area = view.page_visible.area
        doc_w, doc_h = view_area.w, view_area.h
        logger.info(string.format("PanelZoom: Using View area dimensions - w:%d, h:%d", doc_w, doc_h))
    else
        -- Fallback to document dimensions
        local dim = self.ui.document:getNativePageDimensions(page) or self.ui.document:getPageSize(page)
        if not dim then 
            logger.warn("PanelZoom: Could not get page dimensions")
            return false 
        end
        doc_w, doc_h = dim.w, dim.h
        logger.info(string.format("PanelZoom: Using document dimensions - w:%d, h:%d", doc_w, doc_h))
    end

    local rect = {
        x = math.floor((panel.x or 0) * doc_w),
        y = math.floor((panel.y or 0) * doc_h),
        w = math.ceil((panel.w or 1) * doc_w),
        h = math.ceil((panel.h or 1) * doc_h)
    }
    
    logger.info(string.format("PanelZoom: Panel rect - x:%d, y:%d, w:%d, h:%d", rect.x, rect.y, rect.w, rect.h))
    
    -- Close previous viewer BEFORE creating new image to avoid memory issues
    if self._current_imgviewer then 
        logger.info("PanelZoom: Closing previous ImageViewer")
        UIManager:close(self._current_imgviewer)
        self._current_imgviewer = nil
    end
    
    -- Create new image for the panel
    local image = self.ui.document:drawPagePart(page, rect, 0)
    if not image then 
        logger.warn("PanelZoom: Could not draw page part")
        return false 
    end
    
    logger.info("PanelZoom: Successfully created panel image")

    -- Check if we're updating an existing viewer or creating a new one
    if self._current_imgviewer then
        -- Update existing viewer to avoid flicker
        logger.info("PanelZoom: Updating existing ImageViewer")
        self._current_imgviewer.image = image
        self._current_imgviewer:update()
        UIManager:setDirty(self._current_imgviewer, "ui")
    else
        -- Create new viewer
        logger.info("PanelZoom: Creating PanelViewer instance")
        local imgviewer = PanelViewer:new{
            image = image,
            image_disposable = false, -- Don't dispose memory at all
            with_title_bar = false,
            fullscreen = true,
            buttons_visible = false, -- Hide buttons to avoid conflicts
            panel_integration = self, -- Pass reference to the integration
        }
        
        self._current_imgviewer = imgviewer
        logger.info("PanelZoom: Showing ImageViewer")
        UIManager:show(imgviewer)
        logger.info("PanelZoom: ImageViewer shown successfully")
    end
    
    return true
end

return PanelZoomIntegration