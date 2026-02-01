--[[
PanelViewer - A custom image viewer designed specifically for panel navigation

This viewer is built from scratch using KOReader's widget system and APIs,
inspired by modern image rendering patterns. It provides optimized panel
viewing with custom padding, gesture handling, and smooth transitions.
]]

local Blitbuffer = require("ffi/blitbuffer")
local Device = require("device")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local InputContainer = require("ui/widget/container/inputcontainer")
local RenderImage = require("ui/renderimage")
local Screen = require("device").screen
local UIManager = require("ui/uimanager")
local logger = require("logger")
local _ = require("gettext")

local PanelViewer = InputContainer:extend{
    -- Core properties
    name = "PanelViewer",
    
    -- Image source (BlitBuffer or file path)
    image = nil,
    file = nil,
    
    -- Display properties
    fullscreen = true,
    buttons_visible = false,
    
    -- Panel-specific properties
    reading_direction = "ltr",
    
    -- Callbacks for navigation
    onNext = nil,
    onPrev = nil,
    onClose = nil,
    
    -- Internal state
    _image_bb = nil,
    _original_size = nil,
    _display_rect = nil,
    _scaled_image_bb = nil, -- Cached scaled image for display
    _is_dirty = false,
}

function PanelViewer:init()
    -- Initialize touch zones for navigation
    self:setupTouchZones()
    
    -- Load and process the image
    self:loadImage()
    
    -- Calculate display dimensions
    self:calculateDisplayRect()
    
    logger.info(string.format("PanelViewer: Initialized with image %dx%d", 
        self._original_size and self._original_size.w or 0,
        self._original_size and self._original_size.h or 0))
end

function PanelViewer:setupTouchZones()
    local screen_width = Screen:getWidth()
    local screen_height = Screen:getHeight()
    
    -- Define tap zones: Left 30% (prev), Right 30% (next), Center 40% (close)
    self.ges_events = {
        Tap = {
            GestureRange:new{
                ges = "tap",
                range = Geom:new{
                    x = 0, y = 0,
                    w = screen_width,
                    h = screen_height
                }
            }
        }
    }
end

function PanelViewer:loadImage()
    if not self.image and not self.file then
        logger.warn("PanelViewer: No image or file provided")
        return false
    end
    
    local image_bb = nil
    
    -- Load from BlitBuffer
    if self.image then
        image_bb = self.image
        logger.info("PanelViewer: Using provided BlitBuffer")
    -- Load from file with dithering enabled for E-ink optimization
    elseif self.file then
        logger.info(string.format("PanelViewer: Loading image from file with dithering: %s", self.file))
        image_bb = RenderImage:renderImageFile(self.file, false) -- true enables dithering
        if not image_bb then
            logger.error("PanelViewer: Failed to load image file")
            return false
        end
    end
    
    self._image_bb = image_bb
    self._original_size = {
        w = image_bb:getWidth(),
        h = image_bb:getHeight()
    }
    
    return true
end

function PanelViewer:calculateDisplayRect()
    if not self._image_bb then return end
    
    local screen_w = Screen:getWidth()
    local screen_h = Screen:getHeight()
    local img_w = self._original_size.w
    local img_h = self._original_size.h
    
    -- Helper function for round-half-up (pixel-perfect symmetric centering)
    local function round(x)
        return math.floor(x + 0.5)
    end
    
    -- Calculate scale to fit screen while maintaining aspect ratio
    local scale_w = screen_w / img_w
    local scale_h = screen_h / img_h
    local scale = math.min(scale_w, scale_h)
    
    -- Calculate display dimensions (final screen size)
    local display_w = math.floor(img_w * scale)
    local display_h = math.floor(img_h * scale)
    
    -- Center the image on screen with pixel-perfect symmetric centering
    -- Use round-half-up instead of floor to prevent left/top bias
    local display_x = round((screen_w - display_w) / 2)
    local display_y = round((screen_h - display_h) / 2)
    
    self._display_rect = {
        x = display_x,
        y = display_y,
        w = display_w,
        h = display_h
    }
    
    -- BEST: No post-scaling! Use original image directly for 1:1 blitting
    -- The image should be rendered at final size during creation
    self._scaled_image_bb = self._image_bb
    
    logger.info(string.format("PanelViewer: Display rect %dx%d at (%d,%d) scale=%.3f (1:1 blit)", 
        display_w, display_h, display_x, display_y, scale))
end

function PanelViewer:onTap(_, ges)
    if not ges or not ges.pos then return false end
    
    local screen_w = Screen:getWidth()
    local x_pct = ges.pos.x / screen_w
    
    -- Determine direction based on reading direction
    local is_rtl = self.reading_direction == "rtl"
    
    -- Zone Logic: In RTL, Left is "Forward". In LTR, Right is "Forward".
    local is_forward = (is_rtl and x_pct < 0.3) or (not is_rtl and x_pct > 0.7)
    local is_backward = (is_rtl and x_pct > 0.7) or (not is_rtl and x_pct < 0.3)
    
    if is_forward then
        logger.info("PanelViewer: Forward tap detected")
        if self.onNext then self.onNext() end
        return true
    elseif is_backward then
        logger.info("PanelViewer: Backward tap detected")
        if self.onPrev then self.onPrev() end
        return true
    end
    
    -- Center tap: Close the viewer
    logger.info("PanelViewer: Center tap detected, closing viewer")
    if self.onClose then self.onClose() end
    return true
end

function PanelViewer:paintTo(bb, x, y)
    if not self._image_bb or not self._scaled_image_bb then return end
    
    -- Get screen-space rectangle (single source of truth)
    local screen_rect = self:getScreenRect()
    local screen_w = Screen:getWidth()
    local screen_h = Screen:getHeight()
    local white_color = Blitbuffer.Color8(255)
    
    -- Background painting: pure screen coordinates
    -- Paint top area above image
    if screen_rect.y > 0 then
        bb:paintRect(0, 0, screen_w, screen_rect.y, white_color)
    end
    
    -- Paint bottom area below image
    if screen_rect.y + screen_rect.h < screen_h then
        bb:paintRect(0, screen_rect.y + screen_rect.h, screen_w, screen_h - (screen_rect.y + screen_rect.h), white_color)
    end
    
    -- Paint left area
    if screen_rect.x > 0 then
        bb:paintRect(0, screen_rect.y, screen_rect.x, screen_rect.h, white_color)
    end
    
    -- Paint right area
    if screen_rect.x + screen_rect.w < screen_w then
        bb:paintRect(screen_rect.x + screen_rect.w, screen_rect.y, screen_w - (screen_rect.x + screen_rect.w), screen_rect.h, white_color)
    end
    
    -- Image painting: pure screen coordinates with dithering for E-ink optimization

    bb:blitFrom(self._scaled_image_bb, screen_rect.x, screen_rect.y, 0, 0, screen_rect.w, screen_rect.h)

    
    self._is_dirty = false
end

function PanelViewer:getScreenRect()
    -- Single source of truth for screen-space coordinates
    -- Future-proof: supports animations, transforms, partial redraws
    if not self._display_rect then
        -- Fallback: full screen
        return {
            x = 0,
            y = 0,
            w = Screen:getWidth(),
            h = Screen:getHeight()
        }
    end
    
    return {
        x = self._display_rect.x,
        y = self._display_rect.y,
        w = self._display_rect.w,
        h = self._display_rect.h
    }
end

function PanelViewer:getSize()
    return Geom:new{
        x = 0,
        y = 0,
        w = Screen:getWidth(),
        h = Screen:getHeight()
    }
end

function PanelViewer:updateImage(new_image)
    -- Update the image source
    if self._image_bb and self._image_bb ~= self.image then
        self._image_bb:free()
    end
    
    self.image = new_image
    self._image_bb = new_image
    self:loadImage()
    self:calculateDisplayRect()
    self._is_dirty = true
    
    logger.info("PanelViewer: Image updated")
end

function PanelViewer:update()
    -- Mark for repaint
    self._is_dirty = true
    UIManager:setDirty(self, "ui")
    logger.info("PanelViewer: Update called, marking dirty")
end

function PanelViewer:updateReadingDirection(direction)
    self.reading_direction = direction or "ltr"
    logger.info(string.format("PanelViewer: Reading direction set to %s", self.reading_direction))
end

function PanelViewer:freeResources()
    -- BEST: No separate scaled image to free (1:1 blitting)
    -- Only free the original if it's not externally managed
    if self._image_bb and self._image_bb ~= self.image then
        self._image_bb:free()
        self._image_bb = nil
    end
    self._scaled_image_bb = nil  -- Just clear the reference
    logger.info("PanelViewer: Resources freed (1:1 blit mode)")
end

function PanelViewer:close()
    self:freeResources()
    UIManager:close(self)
end

return PanelViewer
