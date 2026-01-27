#!/usr/bin/env python3
"""
Manga Processing Script with Pydantic V2 Schema Enforcement
Processes manga folders/archives to generate panel data with Kumiko.

Usage:
    python process_manga.py [input_path]

Features:
- Creates Pages folder if needed
- Extracts CBZ/ZIP archives to separate folders
- Processes each folder with Kumiko
- Generates JSON files with normalized coordinates
- Supports RTL reading direction
- Pydantic V2 schema validation for data integrity
"""

import os
import sys
import subprocess
import shutil
import zipfile
import argparse
import json
import cv2
from pathlib import Path
from typing import List, Optional, Dict, Any, Union

# ============================================================================
# SCHEMA DEFINITIONS (for reference, not enforced)
# ============================================================================

# Panel coordinates schema (normalized 0-1 range)
class PanelCoordinates:
    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

# Page data schema
class PageData:
    def __init__(self, page: int, image: str, panels: List[PanelCoordinates]):
        self.page = page
        self.image = image
        self.panels = panels
    
    def panel_count(self) -> int:
        """Return the number of panels on this page."""
        return len(self.panels)
    
    def total_panel_area(self) -> float:
        """Calculate total area covered by panels on this page."""
        return sum(panel.w * panel.h for panel in self.panels)

# Chapter data schema
class ChapterData:
    def __init__(self, reading_direction: str, total_pages: int, pages: List[PageData]):
        self.reading_direction = reading_direction
        self.total_pages = total_pages
        self.pages = pages
    
    def get_page(self, page_num: int) -> Optional[PageData]:
        """Get page data by page number."""
        for page in self.pages:
            if page.page == page_num:
                return page
        return None
    
    def total_panels(self) -> int:
        """Get total number of panels across all pages."""
        return sum(page.panel_count() for page in self.pages)

# Manga index schema
class MangaIndex:
    def __init__(self, archive_name: str, total_chapters: int, chapters: List[Dict], reading_direction: str):
        self.archive_name = archive_name
        self.total_chapters = total_chapters
        self.chapters = chapters
        self.reading_direction = reading_direction

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def export_schema(output_file: str = "manga_schema.json"):
    """Export basic schema structure to JSON file for documentation."""
    schema = {
        "description": "Manga panel data structure (without Pydantic validation)",
        "PanelCoordinates": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "y": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "w": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "h": {"type": "number", "minimum": 0.0, "maximum": 1.0}
            }
        },
        "PageData": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "image": {"type": "string"},
                "panels": {"type": "array", "items": {"$ref": "#/PanelCoordinates"}}
            }
        },
        "ChapterData": {
            "type": "object",
            "properties": {
                "reading_direction": {"type": "string", "enum": ["rtl", "ltr"]},
                "total_pages": {"type": "integer", "minimum": 0},
                "pages": {"type": "array", "items": {"$ref": "#/PageData"}}
            }
        }
    }
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        print(f"📄 Schema exported to {output_file}")
        return True
    except Exception as e:
        print(f"❌ Failed to export schema: {e}")
        return False

def validate_json_file(json_file: Path) -> bool:
    """Validate a single JSON file against the basic schema structure."""
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Basic structure validation without Pydantic
        if not isinstance(data, dict):
            print(f"❌ {json_file.name} - Invalid JSON structure (not a dict)")
            return False
        
        # Check for master index structure
        if 'archive_name' in data and 'chapters' in data:
            required_fields = ['archive_name', 'total_chapters', 'chapters', 'reading_direction']
            for field in required_fields:
                if field not in data:
                    print(f"❌ {json_file.name} - Missing required field: {field}")
                    return False
        else:
            # Check for chapter structure
            required_fields = ['reading_direction', 'total_pages', 'pages']
            for field in required_fields:
                if field not in data:
                    print(f"❌ {json_file.name} - Missing required field: {field}")
                    return False
        
        print(f"✅ {json_file.name} - Valid structure")
        return True
        
    except Exception as e:
        print(f"❌ {json_file.name} - Error reading file: {e}")
        return False

def combine_jsons_to_json(json_files, output_json, chapter_name=None):
    """Combine multiple JSON files into a single JSON with basic structure validation."""
    pages_data = []
    reading_direction = "rtl"  # Default to RTL for manga
    total_panels_found = 0
    total_area_covered = 0.0
    
    print(f"🔄 Processing {len(json_files)} JSON files with schema validation...")
    
    for page_num, json_file in enumerate(sorted(json_files), 1):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Try to find the corresponding image file for dimension reading
            image_path = None
            json_stem = json_file.stem
            # Look for image file with same name in common locations
            for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
                potential_image = json_file.parent / f"{json_stem}{ext}"
                if potential_image.exists():
                    image_path = potential_image
                    break
                potential_image = json_file.parent / f"{json_stem}{ext.upper()}"
                if potential_image.exists():
                    image_path = potential_image
                    break
            
            # Handle single page JSON or array of pages
            if isinstance(data, list):
                # Array of page data
                for page_data in data:
                    if 'panels' in page_data and page_data['panels']:
                        # Convert field names and panel formats
                        processed_data = preprocess_page_data(page_data, page_num, image_path)
                        if processed_data:
                            # Add processed data directly (no Pydantic validation)
                            pages_data.append(processed_data)
                            # Calculate statistics
                            panel_count = len(processed_data.get('panels', []))
                            total_panels_found += panel_count
                            total_area_covered += sum(
                                panel.get('w', 0) * panel.get('h', 0) 
                                for panel in processed_data.get('panels', [])
                            )
                            
            elif isinstance(data, dict):
                if 'pages' in data:
                    # Multi-page JSON structure
                    for i, page_data in enumerate(data['pages']):
                        if 'panels' in page_data and page_data['panels']:
                            # Convert field names and panel formats
                            processed_data = preprocess_page_data(page_data, i + 1, image_path)
                            if processed_data:
                                # Add processed data directly (no Pydantic validation)
                                pages_data.append(processed_data)
                                # Calculate statistics
                                panel_count = len(processed_data.get('panels', []))
                                total_panels_found += panel_count
                                total_area_covered += sum(
                                    panel.get('w', 0) * panel.get('h', 0) 
                                    for panel in processed_data.get('panels', [])
                                )
                                    
                elif 'panels' in data and data['panels']:
                    # Single page JSON structure
                    # Convert field names and panel formats
                    processed_data = preprocess_page_data(data, page_num, image_path)
                    if processed_data:
                        # Add processed data directly (no Pydantic validation)
                        pages_data.append(processed_data)
                        # Calculate statistics
                        panel_count = len(processed_data.get('panels', []))
                        total_panels_found += panel_count
                        total_area_covered += sum(
                            panel.get('w', 0) * panel.get('h', 0) 
                            for panel in processed_data.get('panels', [])
                        )
            
        except Exception as e:
            print(f"   ⚠️  Error reading {json_file}: {e}")
            continue
    
    if not pages_data:
        print(f"   ❌ No valid page data found in JSON files")
        return False
    
    # Sort pages by page number (cleaner math with lambda)
    pages_data.sort(key=lambda x: x.get('page', 0))
    
    # Create output structure without Pydantic
    output_data = {
        "reading_direction": reading_direction,
        "total_pages": len(pages_data),
        "pages": pages_data
    }
    
    # Additional statistics with cleaner math
    avg_panels_per_page = total_panels_found / len(pages_data) if pages_data else 0
    avg_area_per_panel = total_area_covered / total_panels_found if total_panels_found > 0 else 0
    
    print(f"   📊 Statistics:")
    print(f"      - Total pages: {len(pages_data)}")
    print(f"      - Total panels: {total_panels_found}")
    print(f"      - Avg panels/page: {avg_panels_per_page:.2f}")
    print(f"      - Total area coverage: {total_area_covered:.3f}")
    print(f"      - Avg area/panel: {avg_area_per_panel:.6f}")
    
    # Create output structure without shrink-wrapping
    output_data = {
        "reading_direction": reading_direction,
        "total_pages": len(pages_data),
        "pages": pages_data
    }
    
    # Write output JSON
    try:
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        print(f"   ✅ Combined {len(json_files)} JSON files into {output_json}")
        return True
    except Exception as e:
        print(f"   ❌ Error writing {output_json}: {e}")
        return False

def preprocess_page_data(page_data, page_num, image_path=None):
    """Preprocess page data to match Pydantic schema requirements."""
    try:
        # Convert filename to image field name
        if 'filename' in page_data:
            page_data['image'] = page_data.pop('filename')
        
        # Add page number if missing
        if 'page' not in page_data:
            page_data['page'] = page_num
        
        # Get image dimensions for normalization
        img_width = None
        img_height = None
        
        # Check if size field exists
        if 'size' in page_data and isinstance(page_data['size'], list) and len(page_data['size']) >= 2:
            img_width, img_height = page_data['size'][0], page_data['size'][1]
        elif image_path and image_path.exists():
            # Try to read actual image file dimensions
            try:
                from PIL import Image
                with Image.open(image_path) as img:
                    img_width, img_height = img.size
                    print(f"   📐 Read image dimensions from {image_path.name}: {img_width}x{img_height}")
            except ImportError:
                print(f"   ⚠️  PIL/Pillow not available, cannot read image dimensions")
            except Exception as e:
                print(f"   ⚠️  Could not read image {image_path}: {e}")
        
        # Convert panel lists to dictionaries and normalize coordinates
        if 'panels' in page_data and isinstance(page_data['panels'], list):
            converted_panels = []
            for panel in page_data['panels']:
                if isinstance(panel, list) and len(panel) >= 4:
                    # Raw pixel coordinates [x, y, w, h]
                    raw_x, raw_y, raw_w, raw_h = float(panel[0]), float(panel[1]), float(panel[2]), float(panel[3])
                    
                    # Normalize if we have image dimensions
                    if img_width and img_height and img_width > 0 and img_height > 0:
                        norm_x = raw_x / img_width
                        norm_y = raw_y / img_height
                        norm_w = raw_w / img_width
                        norm_h = raw_h / img_height
                    else:
                        # If no dimensions available, assume they're already normalized or use defaults
                        # This is a fallback - ideally we should always have dimensions
                        norm_x, norm_y, norm_w, norm_h = raw_x, raw_y, raw_w, raw_h
                        
                        # If values are clearly pixel values (greater than 1), warn and skip normalization
                        if raw_x > 1 or raw_y > 1 or raw_w > 1 or raw_h > 1:
                            print(f"   ⚠️  Warning: Pixel coordinates detected but no image dimensions available")
                            print(f"       Panel: [{raw_x}, {raw_y}, {raw_w}, {raw_h}]")
                            # Skip this panel as we can't normalize properly
                            continue
                    
                    # Create normalized panel dictionary
                    panel_dict = {
                        'x': norm_x,
                        'y': norm_y, 
                        'w': norm_w,
                        'h': norm_h
                    }
                    converted_panels.append(panel_dict)
                elif isinstance(panel, dict):
                    # Already in dictionary format - ensure normalization
                    if 'x' in panel and 'y' in panel and 'w' in panel and 'h' in panel:
                        x, y, w, h = float(panel['x']), float(panel['y']), float(panel['w']), float(panel['h'])
                        
                        # Normalize if values are clearly pixel coordinates
                        if img_width and img_height and img_width > 0 and img_height > 0:
                            if x > 1 or y > 1 or w > 1 or h > 1:
                                panel['x'] = x / img_width
                                panel['y'] = y / img_height
                                panel['w'] = w / img_width
                                panel['h'] = h / img_height
                        
                        converted_panels.append(panel)
            page_data['panels'] = converted_panels
        
        return page_data
    
    except Exception as e:
        print(f"   ⚠️  Error preprocessing page data: {e}")
        return None

def create_kumiko_directories():
    """Create Pages and panel_result folders in Kumiko directory."""
    # Get the directory where this script is located (Kumiko folder)
    kumiko_dir = Path(__file__).parent
    pages_dir = kumiko_dir / "Pages"
    panel_result_dir = kumiko_dir / "panel_result"
    
    pages_dir.mkdir(exist_ok=True)
    panel_result_dir.mkdir(exist_ok=True)
    
    print(f"✅ Created/verified Kumiko directories:")
    print(f"   Pages: {pages_dir.absolute()}")
    print(f"   panel_result: {panel_result_dir.absolute()}")
    
    return pages_dir, panel_result_dir

def detect_file_type(file_path):
    """Detect actual file type using file command."""
    try:
        result = subprocess.run(['file', str(file_path)], 
                              capture_output=True, text=True, check=True)
        output = result.stdout.strip()
        
        if 'Zip archive' in output:
            return 'zip'
        elif 'RAR archive' in output:
            return 'rar'
        elif '7-zip' in output:
            return '7z'
        elif 'tar' in output.lower():
            return 'tar'
        elif 'gzip compressed' in output:
            return 'gzip'
        else:
            print(f"🔍 File detection: {output}")
            return None
            
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️  File type detection not available")
        return None

def extract_archive(archive_path, extract_to):
    """Extract archive with file type detection."""
    archive_path = Path(archive_path)
    extract_to = Path(extract_to)
    extract_to.mkdir(parents=True, exist_ok=True)
    
    # Detect actual file type
    detected_type = detect_file_type(archive_path)
    suffix = archive_path.suffix.lower()
    
    print(f"🔍 File: {archive_path.name} (ext: {suffix}, detected: {detected_type})")
    
    # Use detected type if available, otherwise fall back to extension
    if detected_type:
        archive_type = detected_type
    elif suffix in ['.cbz', '.zip']:
        archive_type = 'zip'
    elif suffix in ['.rar']:
        archive_type = 'rar'
    elif suffix in ['.gz', '.gzip']:
        archive_type = 'gzip'
    else:
        print(f"❌ Unsupported format: {suffix}")
        return False
    
    if archive_type == 'zip':
        try:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
            print(f"✅ Extracted {archive_path.name}")
            return True
        except Exception as e:
            print(f"❌ Failed to extract ZIP: {e}")
            if "not a zip file" in str(e):
                print(f"⚠️  File is not actually a ZIP archive despite .cbz extension!")
            return False
    
    elif archive_type == 'gzip':
        # Handle gzip compressed files (likely .tar.gz renamed to .cbz)
        try:
            # Try to extract as tar.gz first
            cmd = ['tar', '-xzf', str(archive_path), '-C', str(extract_to)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"✅ Extracted gzip/tar.gz {archive_path.name}")
            return True
        except subprocess.CalledProcessError:
            # If tar.gz fails, try just gzip decompression
            try:
                output_file = extract_to / archive_path.stem
                cmd = ['gunzip', '-c', str(archive_path)]
                with open(output_file, 'wb') as f:
                    subprocess.run(cmd, stdout=f, check=True)
                print(f"✅ Decompressed gzip {archive_path.name}")
                return True
            except Exception as e:
                print(f"❌ Failed to extract gzip: {e}")
                print(f"⚠️  File appears to be gzip but extraction failed")
                return False
    
    elif suffix in ['.rar']:
        # Extract RAR files using unrar
        try:
            cmd = ['unrar', 'x', str(archive_path), str(extract_to)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"✅ Extracted RAR {archive_path.name} to {extract_to}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to extract RAR {archive_path}: {e}")
            print(f"   Error output: {e.stderr}")
            return False
        except FileNotFoundError:
            print(f"❌ 'unrar' command not found. Please install unrar:")
            print(f"   Ubuntu/Debian: sudo apt install unrar")
            print(f"   Arch: sudo pacman -S unrar")
            return False
    
    elif suffix in ['.7z']:
        # Extract 7Z files using 7z
        try:
            cmd = ['7z', 'x', str(archive_path), f'-o{extract_to}', '-y']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"✅ Extracted 7Z {archive_path.name} to {extract_to}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to extract 7Z {archive_path}: {e}")
            print(f"   Error output: {e.stderr}")
            return False
        except FileNotFoundError:
            print(f"❌ '7z' command not found. Please install p7zip:")
            print(f"   Ubuntu/Debian: sudo apt install p7zip-full")
            print(f"   Arch: sudo pacman -S p7zip")
            return False
    
    elif suffix in ['.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz']:
        # Extract TAR files using tar
        try:
            cmd = ['tar', '-xf', str(archive_path), '-C', str(extract_to)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"✅ Extracted TAR {archive_path.name} to {extract_to}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to extract TAR {archive_path}: {e}")
            print(f"   Error output: {e.stderr}")
            return False
    
    else:
        print(f"❌ Unsupported archive format: {suffix}")
        print(f"   Supported formats: .cbz, .zip, .rar, .7z, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz")
        return False

def is_archive(file_path):
    """Check if file is a supported archive."""
    return file_path.suffix.lower() in ['.cbz', '.zip', '.rar', '.7z', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz']

def process_image_with_kumiko(image_path, output_dir):
    """Process a single image with Kumiko to generate JSON output directly."""
    image_name = image_path.stem
    output_json = output_dir / f"{image_name}.json"
    
    # Try with JSON output directly
    success, json_file = try_kumiko_with_flags(image_path, output_json, ['--rtl'])
    
    if success and json_file.exists():
        return True, json_file
    
    # Fallback: try without any flags
    print(f"   🔄 Trying fallback without flags...")
    success, file = try_kumiko_with_flags(image_path, output_json, [])
    
    return success, file if success else None

def try_kumiko_with_flags(image_path, output_file, flags):
    """Try running Kumiko with specific flags."""
    image_name = image_path.stem
    
    # Build Kumiko command with -i for input and -o for output
    cmd = ['python3', 'kumiko', '-i', str(image_path)] + flags + ['-o', str(output_file)]
    
    print(f"   Trying: {' '.join(cmd)}")
    
    try:
        # Change to Kumiko directory for execution
        original_cwd = os.getcwd()
        kumiko_dir = Path(__file__).parent
        os.chdir(kumiko_dir)
        
        # Run Kumiko
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0:
            if output_file.exists():
                print(f"   ✅ Success with flags: {' '.join(flags)}")
                return True, output_file
            else:
                print(f"   ⚠️  File not created: {output_file}")
                print(f"   Kumiko output: {result.stdout}")
                return False, None
        else:
            print(f"   ❌ Failed with flags {' '.join(flags)}:")
            print(f"      Return code: {result.returncode}")
            if result.stderr:
                error_msg = result.stderr.strip()
                if len(error_msg) > 200:
                    error_msg = error_msg[:200] + "..."
                print(f"      Error: {error_msg}")
            return False, None
            
    except subprocess.TimeoutExpired:
        print(f"   ❌ Timeout with flags {' '.join(flags)}")
        return False, None
    except Exception as e:
        print(f"   ❌ Exception with flags {' '.join(flags)}: {e}")
        return False, None
    finally:
        os.chdir(original_cwd)

def convert_json_to_html(json_file, html_file):
    """Convert Kumiko JSON output to HTML format for processing."""
    import json
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Create a simple HTML structure with the panel data
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Kumiko Panel Data</title>
</head>
<body>
    <h1>Panel Data for {json_file.stem}</h1>
    <script>
        var panelData = {json.dumps(data, indent=2)};
    </script>
</body>
</html>"""
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"   ✅ Converted JSON to HTML: {html_file}")
        return True, html_file
        
    except Exception as e:
        print(f"   ❌ JSON to HTML conversion failed: {e}")
        return False, None

def combine_htmls_to_json(html_files, output_json, temp_html_dir, folder_path):
    """Combine multiple HTML files into a single JSON with page-based structure."""
    import json
    import re
    
    pages_data = []
    reading_direction = "rtl"  # Default to RTL for manga
    
    print(f"🔄 Combining {len(html_files)} HTML files to JSON...")
    
    # First, check which HTML files actually exist
    existing_html_files = []
    for html_file in html_files:
        if html_file.exists():
            existing_html_files.append(html_file)
            print(f"   Found: {html_file.name}")
        else:
            print(f"   ❌ Missing: {html_file}")
    
    if not existing_html_files:
        print(f"❌ No HTML files found to process")
        return False
    
    # Debug: Show content of first HTML file
    if existing_html_files:
        first_html = existing_html_files[0]
        print(f"🔍 Debug: First HTML file content preview:")
        try:
            with open(first_html, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"   Size: {len(content)} characters")
                print(f"   First 500 chars: {content[:500]}")
                print(f"   Contains 'panel': {'panel' in content.lower()}")
                print(f"   Contains 'json': {'json' in content.lower()}")
                print(f"   Contains 'coordinates': {'coordinates' in content.lower()}")
        except Exception as e:
            print(f"   Error reading file: {e}")
    
    # Sort HTML files by name to ensure correct page order
    existing_html_files.sort(key=lambda x: x.name)
    
    for page_num, html_file in enumerate(existing_html_files, 1):
        print(f"   Processing page {page_num}: {html_file.name}")
        
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Debug: Show what we're looking for
            print(f"     File size: {len(html_content)} chars")
            
            # Try to extract panel data from HTML using multiple patterns
            panel_patterns = [
                # JSON format in script tags - look for the actual panels array (full array)
                r'"panels":\s*(\[[^\]]*(?:\][^\]]*)*\])',
                r'"panels":\s*(\[\s*\[[^\]]*\]\s*(?:,\s*\[[^\]]*\]\s*)*\])',
                # Script tag JSON
                r'<script[^>]*>.*?var\s+\w+\s*=\s*(\{.*?\});.*?</script>',
                r'<script[^>]*>.*?const\s+\w+\s*=\s*(\{.*?\});.*?</script>',
                r'<script[^>]*>.*?let\s+\w+\s*=\s*(\{.*?\});.*?</script>',
                # Direct JSON in HTML
                r'(\{[^}]*"panels"[^}]*\})',
                r'(\{[^}]*"x"[^}]*"y"[^}]*"w"[^}]*"h"[^}]*\})',
                # Panel coordinates in various formats
                r'panel.*?{.*?x.*?(\d+\.?\d*).*?y.*?(\d+\.?\d*).*?w.*?(\d+\.?\d*).*?h.*?(\d+\.?\d*)}',
                r'x.*?(\d+\.?\d*).*?y.*?(\d+\.?\d*).*?width.*?(\d+\.?\d*).*?height.*?(\d+\.?\d*)',
                r'"x":\s*(\d+\.?\d*),\s*"y":\s*(\d+\.?\d*),\s*"w":\s*(\d+\.?\d*),\s*"h":\s*(\d+\.?\d*)',
                r'x:\s*(\d+\.?\d*),\s*y:\s*(\d+\.?\d*),\s*w:\s*(\d+\.?\d*),\s*h:\s*(\d+\.?\d*)',
                # Array format - capture nested arrays (multiple)
                r'\[\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*(?:,\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*)*',
                r'\[\s*\{\s*"x"\s*:\s*(\d+\.?\d*)\s*,\s*"y"\s*:\s*(\d+\.?\d*)\s*,\s*"w"\s*:\s*(\d+\.?\d*)\s*,\s*"h"\s*:\s*(\d+\.?\d*)\s*\}\s*\]'
            ]
            
            matches = []
            used_pattern = None
            
            for i, pattern in enumerate(panel_patterns):
                pattern_matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
                if pattern_matches:
                    matches = pattern_matches
                    used_pattern = f"Pattern {i+1}: {pattern[:50]}..."
                    print(f"     Found {len(matches)} matches with {used_pattern}")
                    
                    # Debug: Show first match
                    if pattern_matches:
                        first_match = str(pattern_matches[0])
                        if len(first_match) > 200:
                            first_match = first_match[:200] + "..."
                        print(f"     First match: {first_match}")
                    break
            
            if not matches:
                print(f"     ❌ No panel matches found")
                # Try to find any JSON data
                json_patterns = [
                    r'(\{[^{}]*\})',
                    r'(\[[^\[\]]*\])'
                ]
                for pattern in json_patterns:
                    json_matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
                    if json_matches:
                        print(f"     Found {len(json_matches)} JSON-like structures")
                        for i, match in enumerate(json_matches[:3]):  # Show first 3
                            match_str = str(match)
                            if len(match_str) > 100:
                                match_str = match_str[:100] + "..."
                            print(f"       JSON {i+1}: {match_str}")
                # Create empty page data and continue
                # Try to find the actual image file to determine the correct extension
                actual_image_name = html_file.stem + ".jpg"  # default
                
                # First try flat directory structure
                for ext in ['.jpg', '.jpeg', '.png']:
                    for folder in [temp_html_dir, folder_path]:
                        if (folder / (html_file.stem + ext)).exists():
                            actual_image_name = html_file.stem + ext
                            break
                        if (folder / (html_file.stem + ext.upper())).exists():
                            actual_image_name = html_file.stem + ext.upper()
                            break
                    else:
                        continue
                    break
                
                # If not found, try subdirectory structure
                if actual_image_name == html_file.stem + ".jpg":
                    for img_file in folder_path.rglob(f"{html_file.stem}*"):
                        if img_file.is_file() and img_file.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                            actual_image_name = str(img_file.relative_to(folder_path))
                            break
                
                page_data = {
                    "page": page_num,
                    "image": actual_image_name,
                    "panels": []
                }
                pages_data.append(page_data)
                continue
            
            # Extract image dimensions from HTML - prioritize actual image size
            # Try to find the actual image file to get dimensions using PIL
            actual_img_width, actual_img_height = 800, 1200  # fallback
            
            # Look for the image in the temp directory or original folder with better format support
            # When images are in subdirectories, we need to find the corresponding image file
            image_extensions = ['.jpg', '.jpeg', '.png']
            
            # First try to find image based on HTML file stem (for flat directory structure)
            for ext in image_extensions:
                image_name = html_file.stem + ext
                image_name_upper = html_file.stem + ext.upper()
                
                possible_image_paths = [
                    temp_html_dir / image_name,      # in temp dir
                    folder_path / image_name,        # in original folder
                    temp_html_dir / image_name_upper, # uppercase in temp dir
                    folder_path / image_name_upper,   # uppercase in original folder
                ]
                
                for img_path in possible_image_paths:
                    if img_path.exists():
                        try:
                            from PIL import Image
                            with Image.open(img_path) as img:
                                actual_img_width, actual_img_height = img.size
                                print(f"     Found actual image dimensions: {actual_img_width}x{actual_img_height} from {img_path.name}")
                                break
                        except Exception as e:
                            print(f"     Could not read image {img_path}: {e}")
                else:
                    continue
                break
            
            # If not found, try to find any image file that matches the HTML stem (for subdirectory structure)
            if actual_img_width == 800 and actual_img_height == 1200:  # still using fallback
                print(f"     Trying to find image for {html_file.stem} in subdirectories...")
                for img_file in folder_path.rglob(f"{html_file.stem}*"):
                    if img_file.is_file() and img_file.suffix.lower() in image_extensions:
                        try:
                            from PIL import Image
                            with Image.open(img_file) as img:
                                actual_img_width, actual_img_height = img.size
                                print(f"     Found actual image dimensions: {actual_img_width}x{actual_img_height} from {img_file.relative_to(folder_path)}")
                                break
                        except Exception as e:
                            print(f"     Could not read image {img_file}: {e}")
            
            img_w, img_h = actual_img_width, actual_img_height
            
            page_panels = []
            panels_added = 0
            
            for match in matches:
                try:
                    # Handle different match formats
                    if isinstance(match, str):
                        # Check if it's a panels array like [[430, 56, 160, 291], [231, 56, 192, 2...]
                        if match.strip().startswith('[') and not match.strip().startswith('{'):
                            # Parse as panels array
                            try:
                                panels_array = json.loads(match)
                                if isinstance(panels_array, list):
                                    for panel_coords in panels_array:
                                        if isinstance(panel_coords, list) and len(panel_coords) >= 4:
                                            x, y, w, h = map(float, panel_coords[:4])
                                            panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                                print(f"     Processed {len(panels_array)} panels from array")
                            except json.JSONDecodeError:
                                # Try regex extraction for nested arrays
                                array_pattern = r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]'
                                coord_matches = re.findall(array_pattern, match)
                                for x, y, w, h in coord_matches:
                                    panels_added += add_normalized_panel_to_page(page_panels, float(x), float(y), float(w), float(h), img_w, img_h)
                                print(f"     Processed {len(coord_matches)} panels via regex")
                        else:
                            # Try to parse as JSON first
                            try:
                                data = json.loads(match)
                                if 'panels' in data:
                                    panel_list = data['panels']
                                    if isinstance(panel_list, list):
                                        for panel_data in panel_list:
                                            if isinstance(panel_data, list) and len(panel_data) >= 4:
                                                x, y, w, h = map(float, panel_data[:4])
                                                panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                                            elif isinstance(panel_data, dict) and all(key in panel_data for key in ['x', 'y', 'w', 'h']):
                                                x, y, w, h = map(float, [panel_data['x'], panel_data['y'], panel_data['w'], panel_data['h']])
                                                panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                                    print(f"     Processed {len(panel_list)} panels from JSON")
                                elif 'x' in data and 'y' in data:
                                    panel_list = [data]
                                    for panel_data in panel_list:
                                        if all(key in panel_data for key in ['x', 'y', 'w', 'h']):
                                            x, y, w, h = map(float, [panel_data['x'], panel_data['y'], panel_data['w'], panel_data['h']])
                                            panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                            except json.JSONDecodeError:
                                # Try regex extraction
                                coord_matches = re.findall(r'(\d+\.?\d*)', match)
                                if len(coord_matches) >= 4:
                                    # Process in groups of 4
                                    for i in range(0, len(coord_matches), 4):
                                        if i + 3 < len(coord_matches):
                                            x, y, w, h = map(float, coord_matches[i:i+4])
                                            panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                    else:
                        # Tuple format from regex
                        x, y, w, h = map(float, match[:4])
                        panels_added += add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h)
                        
                except Exception as e:
                    print(f"     Error processing match: {e}")
                    continue
            
            # Create page data structure
            # Try to find the actual image file to determine the correct extension
            actual_image_name = html_file.stem + ".jpg"  # default
            
            # First try flat directory structure
            for ext in ['.jpg', '.jpeg', '.png']:
                for folder in [temp_html_dir, folder_path]:
                    if (folder / (html_file.stem + ext)).exists():
                        actual_image_name = html_file.stem + ext
                        break
                    if (folder / (html_file.stem + ext.upper())).exists():
                        actual_image_name = html_file.stem + ext.upper()
                        break
                else:
                    continue
                break
            
            # If not found, try subdirectory structure
            if actual_image_name == html_file.stem + ".jpg":
                for img_file in folder_path.rglob(f"{html_file.stem}*"):
                    if img_file.is_file() and img_file.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                        actual_image_name = str(img_file.relative_to(folder_path))
                        break
            
            page_data = {
                "page": page_num,
                "image": actual_image_name,
                "panels": page_panels
            }
            pages_data.append(page_data)
            
            print(f"     Added {panels_added} panels for page {page_num}")
        
        except Exception as e:
            print(f"   ❌ Error processing {html_file}: {e}")
            # Create empty page data even on error
            # Try to find the actual image file to determine the correct extension
            actual_image_name = html_file.stem + ".jpg"  # default
            
            # First try flat directory structure
            for ext in ['.jpg', '.jpeg', '.png']:
                for folder in [temp_html_dir, folder_path]:
                    if (folder / (html_file.stem + ext)).exists():
                        actual_image_name = html_file.stem + ext
                        break
                    if (folder / (html_file.stem + ext.upper())).exists():
                        actual_image_name = html_file.stem + ext.upper()
                        break
                else:
                    continue
                break
            
            # If not found, try subdirectory structure
            if actual_image_name == html_file.stem + ".jpg":
                for img_file in folder_path.rglob(f"{html_file.stem}*"):
                    if img_file.is_file() and img_file.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                        actual_image_name = str(img_file.relative_to(folder_path))
                        break
            
            page_data = {
                "page": page_num,
                "image": actual_image_name,
                "panels": []
            }
            pages_data.append(page_data)
            continue
    
    if not pages_data:
        print(f"❌ No page data created")
        return False
    
    # Count total panels
    total_panels = sum(len(page_data["panels"]) for page_data in pages_data)
    print(f"📊 Total pages: {len(pages_data)}, Total panels: {total_panels}")
    
    # Create final JSON structure with pages array
    json_data = {
        "reading_direction": reading_direction,
        "total_pages": len(pages_data),
        "pages": pages_data
    }
    
    # Write JSON output
    try:
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Combined {total_panels} panels from {len(pages_data)} pages to {output_json}")
        return True
        
    except Exception as e:
        print(f"❌ Error writing JSON: {e}")
        return False

def add_normalized_panel_to_page(page_panels, x, y, w, h, img_w, img_h):
    """Add a normalized panel to a specific page's panel list."""
    # Normalize to 0-1 range
    normalized_x = x / img_w
    normalized_y = y / img_h
    normalized_w = w / img_w
    normalized_h = h / img_h
    
    # Clamp to valid range
    normalized_x = max(0, min(1, normalized_x))
    normalized_y = max(0, min(1, normalized_y))
    normalized_w = max(0, min(1, normalized_w))
    normalized_h = max(0, min(1, normalized_h))
    
    panel = {
        "x": round(normalized_x, 3),
        "y": round(normalized_y, 3),
        "w": round(normalized_w, 3),
        "h": round(normalized_h, 3)
    }
    
    page_panels.append(panel)
    print(f"       Added panel: x={panel['x']}, y={panel['y']}, w={panel['w']}, h={panel['h']}")
    return 1

def add_normalized_panel(all_panels, x, y, w, h, img_w, img_h):
    """Add a normalized panel to the list."""
    # Normalize to 0-1 range
    normalized_x = x / img_w
    normalized_y = y / img_h
    normalized_w = w / img_w
    normalized_h = h / img_h
    
    # Clamp to valid range
    normalized_x = max(0, min(1, normalized_x))
    normalized_y = max(0, min(1, normalized_y))
    normalized_w = max(0, min(1, normalized_w))
    normalized_h = max(0, min(1, normalized_h))
    
    panel = {
        "x": round(normalized_x, 3),
        "y": round(normalized_y, 3),
        "w": round(normalized_w, 3),
        "h": round(normalized_h, 3)
    }
    
    all_panels.append(panel)
    print(f"       Added panel: x={panel['x']}, y={panel['y']}, w={panel['w']}, h={panel['h']}")
    return 1

def process_chapter_based_archive(folder_path, output_dir):
    """Process a CBZ archive with chapter folders for KOReader compatibility."""
    folder_name = folder_path.name
    
    print(f"🔄 Processing chapter-based archive {folder_name}...")
    
    # Handle nested structure - check if there's a single nested directory
    nested_dirs = [d for d in folder_path.iterdir() if d.is_dir()]
    if len(nested_dirs) == 1:
        nested_dir = nested_dirs[0]
        # Check if the nested directory contains chapter directories
        nested_chapter_dirs = [d for d in nested_dir.iterdir() if d.is_dir()]
        has_chapters_in_nested = any(
            any(list(d.glob(f"*{ext}")) or list(d.glob(f"*{ext.upper()}")) 
                for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'])
            for d in nested_chapter_dirs
        )
        if has_chapters_in_nested:
            print(f"   📁 Using nested directory structure: {nested_dir.name}")
            folder_path = nested_dir
    
    # Find all chapter directories (subdirectories containing images)
    chapter_dirs = []
    for item in folder_path.iterdir():
        if item.is_dir():
            # Check if this directory contains image files
            has_images = False
            for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
                if list(item.glob(f"*{ext}")) or list(item.glob(f"*{ext.upper()}")):
                    has_images = True
                    break
            if has_images:
                chapter_dirs.append(item)
    
    if not chapter_dirs:
        print(f"❌ No chapter directories with images found in {folder_path}")
        return False
    
    # Sort chapter directories for consistent processing
    chapter_dirs.sort(key=lambda x: x.name)
    
    print(f"   Found {len(chapter_dirs)} chapter directories:")
    for chapter_dir in chapter_dirs:
        print(f"     - {chapter_dir.name}/")
    
    # Process each chapter separately
    successful_chapters = 0
    for chapter_dir in chapter_dirs:
        print(f"\n📖 Processing chapter: {chapter_dir.name}")
        
        # Create chapter-specific output
        chapter_json = output_dir / f"{folder_name}_{chapter_dir.name}.json"
        temp_json_dir = output_dir / f"{folder_name}_{chapter_dir.name}_temp"
        temp_json_dir.mkdir(exist_ok=True)
        
        # Get all image files in this chapter
        image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']
        image_files = []
        
        for ext in image_extensions:
            image_files.extend(chapter_dir.glob(f"*{ext}"))
            image_files.extend(chapter_dir.glob(f"*{ext.upper()}"))
        
        # Sort files for consistent processing
        image_files.sort()
        
        if not image_files:
            print(f"   ⚠️  No image files found in {chapter_dir.name}")
            continue
        
        print(f"   Found {len(image_files)} image files in {chapter_dir.name}")
        
        # Process each image in this chapter
        json_files = []
        successful_images = 0
        
        for image_file in image_files:
            success, json_file = process_image_with_kumiko(image_file, temp_json_dir)
            if success and json_file and json_file.exists():
                json_files.append(json_file)
                successful_images += 1
            else:
                print(f"   ⚠️  Failed to process {image_file.name}")
        
        print(f"   Successfully processed {successful_images}/{len(image_files)} images in {chapter_dir.name}")
        
        if not json_files:
            print(f"   ❌ No JSON files were generated for {chapter_dir.name}")
            continue
        
        # Combine all JSON files into single JSON for this chapter
        success = combine_jsons_to_json(json_files, chapter_json, chapter_name=chapter_dir.name)
        
        if success:
            successful_chapters += 1
            print(f"   ✅ Chapter {chapter_dir.name} completed successfully")
        
        # Clean up temporary JSON files
        try:
            import shutil
            shutil.rmtree(temp_json_dir)
            print(f"   🧹 Cleaned up temporary files for {chapter_dir.name}")
        except Exception as e:
            print(f"   ⚠️  Could not clean up temp files for {chapter_dir.name}: {e}")
    
    print(f"\n📊 Successfully processed {successful_chapters}/{len(chapter_dirs)} chapters")
    
    if successful_chapters == 0:
        print(f"❌ No chapters were processed successfully")
        return False
    
    # Create master index with Pydantic validation
    try:
        chapters_data = []
        for chapter_dir in chapter_dirs:
            chapter_json = output_dir / f"{folder_name}_{chapter_dir.name}.json"
            if chapter_json.exists():
                with open(chapter_json, 'r', encoding='utf-8') as f:
                    chapter_data = json.load(f)
                chapters_data.append({
                    "name": chapter_dir.name,
                    "json_file": f"{folder_name}_{chapter_dir.name}.json",
                    "total_pages": chapter_data.get("total_pages", 0)
                })
        
        master_index = MangaIndex(
            archive_name=folder_name,
            total_chapters=successful_chapters,
            chapters=chapters_data,
            reading_direction="rtl"
        )
        
        master_json = output_dir / f"{folder_name}.json"
        with open(master_json, 'w', encoding='utf-8') as f:
            # Convert MangaIndex to dict for JSON serialization
            master_data = {
                "archive_name": master_index.archive_name,
                "total_chapters": master_index.total_chapters,
                "chapters": master_index.chapters,
                "reading_direction": master_index.reading_direction
            }
            json.dump(master_data, f, indent=2, ensure_ascii=False)
        print(f"✅ Created master index: {master_json}")
        return True
    except Exception as e:
        print(f"❌ Error creating master index: {e}")
        return False

def process_with_kumiko(folder_path, output_dir):
    """Process a folder with Kumiko by processing each image separately."""
    folder_name = folder_path.name
    output_json = output_dir / f"{folder_name}.json"
    temp_json_dir = output_dir / f"{folder_name}_temp"
    temp_json_dir.mkdir(exist_ok=True)
    
    print(f"🔄 Processing folder {folder_name} with individual image processing...")
    
    # Get all image files in the folder and subdirectories
    image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']
    image_files = []
    
    for ext in image_extensions:
        # Search in current folder and all subdirectories
        image_files.extend(folder_path.rglob(f"*{ext}"))
        image_files.extend(folder_path.rglob(f"*{ext.upper()}"))
    
    # Sort files for consistent processing
    image_files.sort()
    
    if not image_files:
        print(f"❌ No image files found in {folder_path} or its subdirectories")
        print(f"   Contents of {folder_path}:")
        try:
            for item in folder_path.rglob("*"):
                if item.is_file():
                    print(f"     - {item.relative_to(folder_path)}")
                elif item.is_dir():
                    print(f"     📁 {item.relative_to(folder_path)}/")
        except Exception as e:
            print(f"     Could not list contents: {e}")
        return False
    
    print(f"   Found {len(image_files)} image files in {folder_path} and subdirectories")
    
    # Process each image separately
    json_files = []
    successful_images = 0
    
    for image_file in image_files:
        success, json_file = process_image_with_kumiko(image_file, temp_json_dir)
        if success and json_file and json_file.exists():
            json_files.append(json_file)
            successful_images += 1
        else:
            print(f"   ⚠️  Failed to process {image_file.name}")
    
    print(f"   Successfully processed {successful_images}/{len(image_files)} images")
    
    if not json_files:
        print(f"❌ No JSON files were generated")
        return False
    
    # List all JSON files that were actually created
    print(f"   JSON files created:")
    for json_file in json_files:
        print(f"     - {json_file.name}")
    
    # Combine all JSON files into single JSON
    success = combine_jsons_to_json(json_files, output_json)
    
    # Clean up temporary JSON files
    try:
        import shutil
        shutil.rmtree(temp_json_dir)
        print(f"   🧹 Cleaned up temporary files")
    except Exception as e:
        print(f"   ⚠️  Could not clean up temp files: {e}")
    
    return success

def is_chapter_based_archive(folder_path):
    """Check if a folder contains chapter directories (KOReader-style structure)."""
    if not folder_path.is_dir():
        return False
    
    print(f"   🔍 Checking for chapter-based structure in {folder_path}")
    
    # Count subdirectories that contain images
    chapter_dirs = 0
    image_files_in_root = 0
    
    # First, check if there's a nested structure (e.g., 3/3/ch1, 3/3/ch2)
    nested_dirs = [d for d in folder_path.iterdir() if d.is_dir()]
    
    # If there's only one subdirectory, check inside it for chapters
    if len(nested_dirs) == 1:
        nested_dir = nested_dirs[0]
        print(f"   📁 Found single nested directory: {nested_dir.name}, checking inside...")
        folder_path = nested_dir
    
    for item in folder_path.iterdir():
        if item.is_dir():
            # Check if this subdirectory contains images
            has_images = False
            image_count = 0
            for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
                images = list(item.glob(f"*{ext}")) + list(item.glob(f"*{ext.upper()}"))
                image_count += len(images)
                if images:
                    has_images = True
                    break
            if has_images:
                chapter_dirs += 1
                print(f"   📖 Found chapter directory: {item.name} with {image_count} images")
        elif item.is_file() and item.suffix.lower() in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
            image_files_in_root += 1
    
    print(f"   📊 Found {chapter_dirs} chapter directories and {image_files_in_root} images in root")
    
    # Consider it chapter-based if there are multiple chapter directories
    # Also consider it chapter-based if there's at least 1 chapter directory and few/no images in root
    return chapter_dirs >= 2 or (chapter_dirs >= 1 and image_files_in_root <= 2)

def process_input(input_path, pages_dir, panel_result_dir):
    """Process input path (folder or archive)."""
    input_path = Path(input_path)
    
    if not input_path.exists():
        print(f"❌ Input path does not exist: {input_path}")
        return False
    
    if is_archive(input_path):
        # Extract archive first
        extract_folder = pages_dir / input_path.stem
        extract_folder.mkdir(exist_ok=True)
        
        print(f"📦 Processing archive: {input_path}")
        if not extract_archive(input_path, extract_folder):
            return False
        
        # Check if this is a chapter-based archive (KOReader style)
        if is_chapter_based_archive(extract_folder):
            print(f"📚 Detected chapter-based archive structure")
            return process_chapter_based_archive(extract_folder, panel_result_dir)
        else:
            print(f"📖 Processing as standard archive")
            return process_with_kumiko(extract_folder, panel_result_dir)
        
    elif input_path.is_dir():
        # Check if this is a chapter-based directory
        if is_chapter_based_archive(input_path):
            print(f"📚 Detected chapter-based directory structure")
            return process_chapter_based_archive(input_path, panel_result_dir)
        else:
            # Process folder directly
            print(f"📁 Processing folder: {input_path}")
            return process_with_kumiko(input_path, panel_result_dir)
        
    else:
        print(f"❌ Unsupported input type: {input_path}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Process manga folders/archives with Kumiko and Pydantic V2 validation")
    parser.add_argument('input', nargs='?', help='Input folder or archive file')
    parser.add_argument('--pages-dir', default='Pages', help='Pages directory name (within Kumiko)')
    parser.add_argument('--output-dir', default='panel_result', help='Output directory name (within Kumiko)')
    parser.add_argument('--export-schema', action='store_true', help='Export Pydantic schema to JSON file')
    parser.add_argument('--validate', help='Validate existing JSON file against schema')
    parser.add_argument('--schema-file', default='manga_schema.json', help='Schema file name for export')
    
    args = parser.parse_args()
    
    # Handle schema export
    if args.export_schema:
        print("📄 Exporting Pydantic V2 schema...")
        success = export_schema(args.schema_file)
        sys.exit(0 if success else 1)
    
    # Handle validation
    if args.validate:
        print("� Validating JSON file against schema...")
        json_file = Path(args.validate)
        if not json_file.exists():
            print(f"❌ File not found: {json_file}")
            sys.exit(1)
        
        success = validate_json_file(json_file)
        sys.exit(0 if success else 1)
    
    # Require input for processing
    if not args.input:
        parser.print_help()
        print("\n❌ Input file/folder is required for processing")
        sys.exit(1)
    
    print("�� Manga Processing Script with Pydantic V2 Started")
    print("=" * 50)
    
    # Create directories in Kumiko folder
    pages_dir, panel_result_dir = create_kumiko_directories()
    
    # Process input
    success = process_input(args.input, pages_dir, panel_result_dir)
    
    print("=" * 50)
    if success:
        print("🎉 Processing completed successfully!")
        print(f"📂 Results in: {panel_result_dir.absolute()}")
        
        # Validate output files
        print("\n🔍 Validating output files against Pydantic schema...")
        output_files = list(panel_result_dir.glob("*.json"))
        valid_files = 0
        for output_file in output_files:
            if validate_json_file(output_file):
                valid_files += 1
        
        print(f"📊 Validation Summary: {valid_files}/{len(output_files)} files passed schema validation")
        
    else:
        print("❌ Processing failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
