local Device = require("device")
local Dispatcher = require("dispatcher")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local InfoMessage = require("ui/widget/infomessage")
local Screen = require("device").screen
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local PanelViewer = require("panel_viewer")
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
    _panel_cache = {}, -- Cache JSON per document
    _preloaded_image = nil, -- Pre-rendered next panel
    _preloaded_panel_index = nil, -- Index of preloaded panel
    _is_switching = false, -- Debounce guard to prevent fast tap issues
}

function PanelZoomIntegration:init()
    self:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)

    -- Optional: Re-render current panel if document settings change
    self.onSettingsUpdate = function()
        if self._current_imgviewer and self.integration_mode then
            logger.info("PanelZoom: Settings changed, refreshing current panel")
            self:displayCurrentPanel()
        end
    end
end

-- Callback methods for PanelViewer
function PanelZoomIntegration:nextPanel()
    if self._is_switching then return end -- Block if already processing
    self._is_switching = true
    
    -- Reset the flag after the UI has had a chance to breathe
    UIManager:scheduleIn(0.3, function() self._is_switching = false end)
    
    -- Check if we have preloaded the next panel
    if self._preloaded_image and self._preloaded_panel_index == self.current_panel_index + 1 then
        -- Use preloaded image for instant switch
        logger.info("PanelZoom: Using preloaded panel for instant switch")
        self.current_panel_index = self.current_panel_index + 1
        self:displayPreloadedPanel()
        return
    end
    
    if self.current_panel_index < #self.current_panels then
        self.current_panel_index = self.current_panel_index + 1
        self:displayCurrentPanel()
    else
        -- Last panel reached, jump to next page
        logger.info("PanelZoom: Last panel reached, jumping to next page")
        self:changePage(1) 
    end
end

function PanelZoomIntegration:prevPanel()
    if self._is_switching then return end -- Block if already processing
    self._is_switching = true
    
    -- Reset the flag after the UI has had a chance to breathe
    UIManager:scheduleIn(0.3, function() self._is_switching = false end)
    
    if self.current_panel_index > 1 then
        self.current_panel_index = self.current_panel_index - 1
        self:displayCurrentPanel()
    else
        -- First panel reached, jump to previous page
        logger.info("PanelZoom: First panel reached, jumping to previous page")
        self:changePage(-1)
    end
end

function PanelZoomIntegration:closeViewer()
    UIManager:close(self._current_imgviewer)
    self._current_imgviewer = nil
    self:cleanupPreloadedImage()
end

-- Preload the next panel in background
function PanelZoomIntegration:preloadNextPanel()
    -- Clean up any existing preloaded image
    self:cleanupPreloadedImage()
    
    -- Check if there's a next panel to preload
    if self.current_panel_index < #self.current_panels then
        local next_panel_index = self.current_panel_index + 1
        local next_panel = self.current_panels[next_panel_index]
        
        if next_panel then
            logger.info(string.format("PanelZoom: Preloading panel %d in background", next_panel_index))
            
            local page = self:getSafePageNumber()
            local dim = self.ui.document:getNativePageDimensions(page)
            
            if dim then
                -- Use helper function for center-preserving quantization
                local rect = self:panelToRect(next_panel, dim)
                
                -- Render the next panel with document settings
                local image, rotate = self:drawPagePartWithSettings(page, rect)
                if image then
                    self._preloaded_image = image
                    self._preloaded_panel_index = next_panel_index
                    logger.info("PanelZoom: Successfully preloaded next panel with document settings")
                else
                    logger.warn("PanelZoom: Failed to preload next panel")
                end
            end
        end
    end
end

-- Display preloaded panel instantly
function PanelZoomIntegration:displayPreloadedPanel()
    if not self._preloaded_image or not self._current_imgviewer then
        logger.warn("PanelZoom: No preloaded image or viewer available")
        return false
    end
    
    logger.info("PanelZoom: Displaying preloaded panel instantly")
    
    -- Update existing viewer with preloaded image using PanelViewer's method
    self._current_imgviewer:updateImage(self._preloaded_image)
    self._current_imgviewer:update()
    UIManager:setDirty(self._current_imgviewer, "ui")
    
    -- Clear preloaded image after use
    self._preloaded_image = nil
    self._preloaded_panel_index = nil
    
    -- Start preloading the next panel
    UIManager:scheduleIn(0.1, function()
        self:preloadNextPanel()
    end)
    
    return true
end

-- Custom drawPagePart that applies document settings
function PanelZoomIntegration:drawPagePartWithSettings(pageno, rect)
    -- 1. Fetch current document settings
    local doc_cfg = self.ui.document.info.config or {}
    local gamma = self.ui.view.state.gamma or doc_cfg.gamma or 1.0
    local contrast = doc_cfg.contrast or 1.0
    
    -- 2. OPTIMIZATION: Render at SCREEN RESOLUTION for 1:1 blitting
    -- Calculate the scale needed to fit the panel to screen
    local Screen = require("device").screen
    local screen_w = Screen:getWidth()
    local screen_h = Screen:getHeight()
    
    -- Calculate scale to fit panel to screen while maintaining aspect ratio
    local scale_w = screen_w / rect.w
    local scale_h = screen_h / rect.h
    local final_scale = math.min(scale_w, scale_h)
    
    -- Calculate final display size on screen
    local display_w = math.floor(rect.w * final_scale)
    local display_h = math.floor(rect.h * final_scale)
    
    -- OPTIMIZED: Render directly at final screen size
    -- This eliminates post-scaling blur and improves performance
    local zoom = final_scale
    
    local geom_rect = Geom:new(rect)
    local scaled_rect = geom_rect:copy()
    scaled_rect:transformByScale(zoom, zoom)
    rect.scaled_rect = scaled_rect
    
    -- 3. Render the base image at FINAL SCREEN SIZE
    -- BEST: No post-scaling needed - perfect 1:1 blitting
    local tile = self.ui.document:renderPage(pageno, rect, zoom, 0, gamma, true)
    local image = tile.bb
    
    -- 4. Apply Post-Processing (Contrast and Inversion)
    if image then
        -- Apply contrast if it's not neutral (1.0)
        if contrast ~= 1.0 and image.contrast then
            image:contrast(contrast)
            logger.info(string.format("PanelZoom: Applied contrast %.2f", contrast))
        end
        
        -- Apply software inversion (Night Mode / Invert Document)
        if doc_cfg.invert and image.invert then
            image:invert()
            logger.info("PanelZoom: Applied image inversion")
        end
        
        logger.info(string.format("PanelZoom: Rendered at final size %dx%d (scale=%.3f) - 1:1 blit ready", 
            display_w, display_h, final_scale))
    end

    return image, false  -- No rotation needed for 1:1 blitting
end

-- Apply KOReader's contrast and gamma settings to image buffer
-- This can be used for preloaded images or manual refreshes
function PanelZoomIntegration:applyDocumentSettings(image)
    if not image then return false end
    
    local doc_cfg = self.ui.document.info.config or {}
    local contrast = doc_cfg.contrast or 1.0
    local gamma = self.ui.view.state.gamma or doc_cfg.gamma or 1.0
    
    -- Contrast
    if image.contrast and contrast ~= 1.0 then
        image:contrast(contrast)
        logger.info(string.format("PanelZoom: Applied contrast %.2f", contrast))
    end
    
    -- Gamma (if not handled during renderPage)
    if image.gamma and gamma ~= 1.0 then
        image:gamma(gamma)
        logger.info(string.format("PanelZoom: Applied gamma %.2f", gamma))
    end
    
    -- Invert
    if image.invert and doc_cfg.invert then
        image:invert()
        logger.info("PanelZoom: Applied image inversion")
    end
    
    return true
end

-- Clean up preloaded image to prevent memory leaks
function PanelZoomIntegration:cleanupPreloadedImage()
    if self._preloaded_image then
        logger.info("PanelZoom: Cleaning up preloaded image")
        self._preloaded_image = nil
        self._preloaded_panel_index = nil
    end
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
        
        -- Clear preloaded cache after page is fully loaded to prevent conflicts
        self:cleanupPreloadedImage()
        
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
    
    -- Extract directory and base name early for use in cache check
    local dir, filename = util.splitFilePathName(doc_path)
    local base_name = filename:match("(.+)%..+$") or filename
    
    -- Check cache first
    if self._panel_cache[doc_path] then
        logger.info("PanelZoom: Using cached JSON for " .. doc_path)
        local cached_data = self._panel_cache[doc_path]
        self.reading_direction = cached_data.reading_direction or "ltr"
        
        -- Check if this is a chapter-based master index in cache
        if cached_data.chapters and type(cached_data.chapters) == "table" and #cached_data.chapters > 0 then
            logger.info("PanelZoom: Using cached chapter-based archive structure")
            return self:loadChapterBasedPanels(cached_data, dir, base_name)
        end
        
        local page_idx = self:getSafePageNumber()
        local panels = nil
        
        -- Find panels for current page from cached data
        if cached_data.pages and type(cached_data.pages) == "table" and #cached_data.pages > 0 then
            for _, page_data in ipairs(cached_data.pages) do
                if page_data.page == page_idx then
                    panels = page_data.panels
                    logger.info(string.format("PanelZoom: Found page %d in cached data", page_idx))
                    break
                end
            end
        end
        
        if panels and #panels > 0 then
            self.current_panels = panels
            logger.info(string.format("PanelZoom: SUCCESS! Loaded %d panels from cache for page %d", #panels, page_idx))
        else
            self.current_panels = {}
            logger.warn(string.format("PanelZoom: Cache found, but no panels match page %d", page_idx))
        end
        return
    end
    
    -- Not in cache, load from file
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

    -- Check if this is a chapter-based master index
    if data.chapters and type(data.chapters) == "table" and #data.chapters > 0 then
        logger.info("PanelZoom: Detected chapter-based archive structure")
        return self:loadChapterBasedPanels(data, dir, base_name)
    end

    -- Cache the parsed JSON
    self._panel_cache[doc_path] = data
    logger.info("PanelZoom: Cached JSON for " .. doc_path)

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

function PanelZoomIntegration:loadChapterBasedPanels(master_data, dir, base_name)
    logger.info("PanelZoom: Loading chapter-based panels")
    
    -- Cache the master data
    local doc_path = self.ui.document.file
    self._panel_cache[doc_path] = master_data
    
    -- Save the reading direction
    self.reading_direction = master_data.reading_direction or "ltr"
    logger.info(string.format("PanelZoom: Reading direction set to %s", self.reading_direction))
    
    local page_idx = self:getSafePageNumber()
    local current_page_in_chapter = page_idx
    local cumulative_pages = 0
    
    -- Find which chapter contains the current page
    local target_chapter = nil
    for _, chapter in ipairs(master_data.chapters) do
        if current_page_in_chapter <= chapter.total_pages then
            target_chapter = chapter
            break
        else
            current_page_in_chapter = current_page_in_chapter - chapter.total_pages
        end
    end
    
    if not target_chapter then
        logger.warn(string.format("PanelZoom: Could not find chapter for page %d", page_idx))
        self.current_panels = {}
        return
    end
    
    logger.info(string.format("PanelZoom: Found chapter %s for page %d (page %d in chapter)", 
        target_chapter.name, page_idx, current_page_in_chapter))
    
    -- Load the chapter JSON file
    local chapter_json_path = dir .. "/" .. target_chapter.json_file
    local chapter_file = io.open(chapter_json_path, "r")
    if not chapter_file then 
        logger.warn("PanelZoom: Chapter JSON not found at " .. chapter_json_path)
        self.current_panels = {}
        return 
    end
    
    local chapter_content = chapter_file:read("*all")
    chapter_file:close()
    
    local ok, chapter_data = pcall(json.decode, chapter_content)
    if not ok or not chapter_data then 
        logger.warn("PanelZoom: Failed to parse chapter JSON")
        self.current_panels = {}
        return 
    end
    
    -- Find panels for the current page within the chapter
    local panels = nil
    if chapter_data.pages and type(chapter_data.pages) == "table" and #chapter_data.pages > 0 then
        for _, page_data in ipairs(chapter_data.pages) do
            if page_data.page == current_page_in_chapter then
                panels = page_data.panels
                logger.info(string.format("PanelZoom: Found page %d in chapter %s", current_page_in_chapter, target_chapter.name))
                break
            end
        end
    end
    
    if panels and #panels > 0 then
        self.current_panels = panels
        logger.info(string.format("PanelZoom: SUCCESS! Loaded %d panels for page %d from chapter %s", 
            #panels, page_idx, target_chapter.name))
    else
        self.current_panels = {}
        logger.warn(string.format("PanelZoom: Chapter JSON found, but no panels match page %d in chapter %s", 
            current_page_in_chapter, target_chapter.name))
    end
end

function PanelZoomIntegration:panelToRect(panel, dim)
    -- Convert panel coordinates to rect with center-preserving quantization
    local cx = (panel.x + panel.w / 2) * dim.w
    local cy = (panel.y + panel.h / 2) * dim.h

    local w = math.floor(panel.w * dim.w + 0.5)
    local h = math.floor(panel.h * dim.h + 0.5)

    local x = math.floor(cx - w / 2 + 0.5)
    local y = math.floor(cy - h / 2 + 0.5)

    -- Add dynamic frame/padding based on panel coordinates and size
    local panel_width = panel.w * dim.w
    local panel_height = panel.h * dim.h
    local panel_x = panel.x * dim.w
    local panel_y = panel.y * dim.h
    
    -- Calculate dynamic padding based on panel size and position
    -- Smaller panels get proportionally more padding, edge panels get less
    local base_padding = math.min(panel_width, panel_height) * 0.05  -- 5% of smaller dimension
    base_padding = math.max(1.0, math.min(2.0, base_padding))  -- Clamp between 2-15px
    
    -- Reduce padding near edges to avoid going out of bounds
    local left_padding = math.min(0.2, panel_x)
    local right_padding = math.min(2.0, dim.w - (panel_x + panel_width))
    local top_padding = math.min(0, panel_y)
    local bottom_padding = math.min(2, dim.h - (panel_y + panel_height))
    
    -- Apply calculated padding
    x = x - left_padding
    y = y - top_padding
    w = w + left_padding + right_padding
    h = h + top_padding + bottom_padding

    return {
        x = x,
        y = y,
        w = w,
        h = h
    }
end

function PanelZoomIntegration:displayCurrentPanel()
    logger.info("PanelZoom: displayCurrentPanel called")
    local panel = self.current_panels[self.current_panel_index]
    if not panel then 
        logger.warn("PanelZoom: No panel data found for index " .. self.current_panel_index)
        return false 
    end

    local page = self:getSafePageNumber()
    
    -- Get dimensions from document for consistent coordinate space
    local dim = self.ui.document:getNativePageDimensions(page) or self.ui.document:getPageSize(page)
    if not dim then 
        logger.warn("PanelZoom: Could not get page dimensions")
        return false 
    end
    logger.info(string.format("PanelZoom: Using document dimensions - w:%d, h:%d", dim.w, dim.h))

    -- Use helper function for center-preserving quantization with dynamic frame
    local rect = self:panelToRect(panel, dim)
    
    logger.info(string.format("PanelZoom: Panel rect - x:%d, y:%d, w:%d, h:%d", rect.x, rect.y, rect.w, rect.h))
    
    -- Create new image for the panel with document settings
    local image, rotate = self:drawPagePartWithSettings(page, rect)
    if not image then 
        logger.warn("PanelZoom: Could not draw page part")
        return false 
    end
    
    logger.info("PanelZoom: Successfully created panel image with document settings")

    -- Close previous viewer BEFORE creating new image to avoid memory issues
    if self._current_imgviewer then 
        logger.info("PanelZoom: Closing previous PanelViewer")
        UIManager:close(self._current_imgviewer)
        self._current_imgviewer = nil
    end
    
    -- Create new PanelViewer instance with our custom implementation
    logger.info("PanelZoom: Creating new PanelViewer instance")
    local panel_viewer = PanelViewer:new{
        image = image,
        fullscreen = true,
        buttons_visible = false,
        reading_direction = self.reading_direction,
        onNext = function() self:nextPanel() end,
        onPrev = function() self:prevPanel() end,
        onClose = function() self:closeViewer() end,
    }
    
    self._current_imgviewer = panel_viewer
    logger.info("PanelZoom: Showing new PanelViewer")
    UIManager:show(panel_viewer)
    logger.info("PanelZoom: New PanelViewer shown successfully")
    
    -- Start preloading the next panel after a short delay
    UIManager:scheduleIn(0.2, function()
        self:preloadNextPanel()
    end)
    
    return true -- Success, new viewer created
end

return PanelZoomIntegration