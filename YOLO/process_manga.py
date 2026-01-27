#!/usr/bin/env python3
"""
Manga Processing Script with Pydantic V2 Schema Enforcement
Processes manga folders/archives to generate panel data with ordered_detection.py.

Usage:
    python process_manga.py [input_path]

Features:
- Creates Pages folder if needed
- Extracts CBZ/ZIP archives to separate folders
- Processes each folder with ordered_detection.py
- Generates JSON files with normalized coordinates
- Supports RTL reading direction
- Pydantic V2 schema validation for data integrity
- OpenCV support for image processing
- Recursive image search in Pages directory
- Comprehensive image format support (.jpg, .jpeg, .png, .bmp, .gif, .webp)
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

# Pydantic V2 imports for schema enforcement
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from pydantic.types import confloat, conint

# ============================================================================
# PYDANTIC V2 SCHEMA DEFINITIONS
# ============================================================================

# Panel coordinates schema (normalized 0-1 range)
class PanelCoordinates(BaseModel):
    x: confloat(ge=0.0, le=1.0)  # Normalized X coordinate
    y: confloat(ge=0.0, le=1.0)  # Normalized Y coordinate
    w: confloat(ge=0.0, le=1.0)  # Normalized width
    h: confloat(ge=0.0, le=1.0)  # Normalized height

# Page data schema
class PageData(BaseModel):
    page: conint(ge=1)           # Page number (1-based)
    image: str                   # Image filename
    panels: List[PanelCoordinates]  # List of panel coordinates
    
    @field_validator('panels')
    @classmethod
    def validate_panels(cls, v):
        if not v:
            raise ValueError('At least one panel is required')
        return v
    
    def panel_count(self) -> int:
        """Return the number of panels on this page."""
        return len(self.panels)
    
    def total_panel_area(self) -> float:
        """Calculate total area covered by panels on this page."""
        return sum(panel.w * panel.h for panel in self.panels)

# Chapter data schema
class ChapterData(BaseModel):
    reading_direction: str = Field(default="rtl", pattern="^(ltr|rtl)$")
    total_pages: conint(ge=0)   # Total number of pages
    pages: List[PageData]        # List of page data
    
    @field_validator('pages')
    @classmethod
    def validate_pages(cls, v):
        if not v:
            raise ValueError('At least one page is required')
        return v
    
    @model_validator(mode='after')
    def validate_page_numbers(self):
        """Ensure page numbers are sequential and start from 1."""
        if self.pages:
            page_numbers = [page.page for page in self.pages]
            if page_numbers != list(range(1, len(page_numbers) + 1)):
                raise ValueError('Page numbers must be sequential starting from 1')
        return self

    def get_page(self, page_num: int) -> Optional[PageData]:
        """Get page data by page number."""
        for page in self.pages:
            if page.page == page_num:
                return page
        return None
    
    def total_panels(self) -> int:
        """Get total number of panels across all pages."""
        return sum(page.panel_count() for page in self.pages)

# Manga index schema for chapter-based archives
class MangaIndex(BaseModel):
    archive_name: str
    total_chapters: conint(ge=0)
    chapters: List[Dict[str, Any]]  # Chapter information
    reading_direction: str = Field(default="rtl", pattern="^(ltr|rtl)$")
    
    @field_validator('chapters')
    @classmethod
    def validate_chapters(cls, v):
        if not v:
            raise ValueError('At least one chapter is required')
        return v

def create_directories():
    """Create Pages and panel_result folders."""
    # Get the directory where this script is located
    script_dir = Path(__file__).parent
    pages_dir = script_dir / "Pages"
    panel_result_dir = script_dir / "panel_result"
    
    pages_dir.mkdir(exist_ok=True)
    panel_result_dir.mkdir(exist_ok=True)
    
    print(f"✅ Created/verified directories:")
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

def process_image_with_ordered_detection(image_path, output_dir):
    """Process a single image with ordered_detection.py to generate panel data."""
    image_name = image_path.stem
    output_json = output_dir / f"{image_name}_panels.json"
    
    # Build ordered_detection.py command with -i flag
    script_dir = Path(__file__).parent
    detection_script = script_dir / "ordered_detection.py"
    
    # Change to script directory and run detection
    cmd = ['python3', str(detection_script), '-i', str(image_path.absolute())]
    
    print(f"   Processing: {image_path.name}")
    
    try:
        # Change to script directory for execution
        original_cwd = os.getcwd()
        os.chdir(script_dir)
        
        # Run ordered_detection.py with -i flag
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0:
            # Check if reading_order.json was created
            reading_order_file = script_dir / "reading_order.json"
            if reading_order_file.exists():
                # Move and rename the output file
                shutil.move(str(reading_order_file), str(output_json))
                print(f"   ✅ Success: {output_json.name}")
                return True, output_json
            else:
                print(f"   ⚠️  No output file created")
                print(f"   Output: {result.stdout}")
                return False, None
        else:
            print(f"   ❌ Failed:")
            print(f"      Return code: {result.returncode}")
            if result.stderr:
                error_msg = result.stderr.strip()
                if len(error_msg) > 200:
                    error_msg = error_msg[:200] + "..."
                print(f"      Error: {error_msg}")
            return False, None
            
    except subprocess.TimeoutExpired:
        print(f"   ❌ Timeout processing {image_path.name}")
        return False, None
    except Exception as e:
        print(f"   ❌ Exception processing {image_path.name}: {e}")
        return False, None
    finally:
        os.chdir(original_cwd)

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

def combine_jsons_to_final_json(json_files, output_json, temp_json_dir, folder_path):
    """Combine multiple JSON files into a single JSON with page-based structure."""
    import json
    
    pages_data = []
    reading_direction = "rtl"  # Default to RTL for manga
    
    print(f"🔄 Combining {len(json_files)} JSON files to final JSON...")
    
    # Check which JSON files actually exist
    existing_json_files = []
    for json_file in json_files:
        if json_file.exists():
            existing_json_files.append(json_file)
            print(f"   Found: {json_file.name}")
        else:
            print(f"   ❌ Missing: {json_file}")
    
    if not existing_json_files:
        print(f"❌ No JSON files found to process")
        return False
    
    # Sort JSON files by name to ensure correct page order
    existing_json_files.sort(key=lambda x: x.name)
    
    for page_num, json_file in enumerate(existing_json_files, 1):
        print(f"   Processing page {page_num}: {json_file.name}")
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Convert reading_order format to page format
            page_panels = []
            if 'reading_order' in data:
                # Get actual image dimensions for proper normalization
                from PIL import Image
                
                # Try to find the actual image file to get dimensions
                actual_img_width, actual_img_height = 800, 1200  # fallback
                
                # Look for the image in the temp directory or original folder with better format support
                image_extensions = ['.jpg', '.jpeg', '.png']
                
                for ext in image_extensions:
                    image_name = json_file.stem.replace('_panels', '') + ext
                    image_name_upper = json_file.stem.replace('_panels', '') + ext.upper()
                    
                    possible_image_paths = [
                        temp_json_dir / image_name,      # in temp dir
                        folder_path / image_name,        # in original folder
                        temp_json_dir / image_name_upper, # uppercase in temp dir
                        folder_path / image_name_upper,   # uppercase in original folder
                    ]
                    
                    for img_path in possible_image_paths:
                        if img_path.exists():
                            try:
                                with Image.open(img_path) as img:
                                    actual_img_width, actual_img_height = img.size
                                    print(f"     Found actual image dimensions: {actual_img_width}x{actual_img_height} from {img_path.name}")
                                    break
                            except Exception as e:
                                print(f"     Could not read image {img_path}: {e}")
                    else:
                        continue
                    break
                
                for panel_data in data['reading_order']:
                    if 'bbox' in panel_data and len(panel_data['bbox']) == 4:
                        # Convert from absolute coordinates to normalized using ACTUAL image dimensions
                        x1, y1, x2, y2 = panel_data['bbox']
                        w = x2 - x1
                        h = y2 - y1
                        
                        # Normalize to 0-1 range using actual image dimensions
                        normalized_x = x1 / actual_img_width
                        normalized_y = y1 / actual_img_height
                        normalized_w = w / actual_img_width
                        normalized_h = h / actual_img_height
                        
                        panel = {
                            "x": round(normalized_x, 3),
                            "y": round(normalized_y, 3),
                            "w": round(normalized_w, 3),
                            "h": round(normalized_h, 3)
                        }
                        page_panels.append(panel)
            
            # Create page data structure
            # Try to find the actual image file to determine the correct extension
            actual_image_name = json_file.stem.replace('_panels', '') + ".jpg"  # default
            for ext in ['.jpg', '.jpeg', '.png']:
                image_name = json_file.stem.replace('_panels', '') + ext
                image_name_upper = json_file.stem.replace('_panels', '') + ext.upper()
                
                possible_image_paths = [
                    temp_json_dir / image_name,      # in temp dir
                    folder_path / image_name,        # in original folder
                    temp_json_dir / image_name_upper, # uppercase in temp dir
                    folder_path / image_name_upper,   # uppercase in original folder
                ]
                
                for img_path in possible_image_paths:
                    if img_path.exists():
                        actual_image_name = img_path.name
                        break
                else:
                    continue
                break
            
            page_data = {
                "page": page_num,
                "image": actual_image_name,
                "panels": page_panels
            }
            pages_data.append(page_data)
            
            print(f"     Added {len(page_panels)} panels for page {page_num}")
        
        except Exception as e:
            print(f"   ❌ Error processing {json_file}: {e}")
            # Create empty page data even on error
            # Try to find the actual image file to determine the correct extension
            actual_image_name = json_file.stem.replace('_panels', '') + ".jpg"  # default
            for ext in ['.jpg', '.jpeg', '.png']:
                image_name = json_file.stem.replace('_panels', '') + ext
                image_name_upper = json_file.stem.replace('_panels', '') + ext.upper()
                
                possible_image_paths = [
                    temp_json_dir / image_name,      # in temp dir
                    folder_path / image_name,        # in original folder
                    temp_json_dir / image_name_upper, # uppercase in temp dir
                    folder_path / image_name_upper,   # uppercase in original folder
                ]
                
                for img_path in possible_image_paths:
                    if img_path.exists():
                        actual_image_name = img_path.name
                        break
                else:
                    continue
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

def process_with_ordered_detection(folder_path, output_dir):
    """Process a folder with ordered_detection.py by processing each image separately."""
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
        success, json_file = process_image_with_ordered_detection(image_file, temp_json_dir)
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
    success = combine_jsons_to_final_json(json_files, output_json, temp_json_dir, folder_path)
    
    # Clean up temporary JSON files
    try:
        import shutil
        shutil.rmtree(temp_json_dir)
        print(f"🧹 Cleaned up temporary files")
    except Exception as e:
        print(f"⚠️  Could not clean up temp files: {e}")
    
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
            success, json_file = process_image_with_ordered_detection(image_file, temp_json_dir)
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
        success = combine_jsons_to_final_json(json_files, chapter_json, temp_json_dir, chapter_dir)
        
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
    
    # Create a master index file that lists all chapters
    master_index = {
        "archive_name": folder_name,
        "total_chapters": successful_chapters,
        "chapters": [],
        "reading_direction": "rtl"
    }
    
    for chapter_dir in chapter_dirs:
        chapter_json = output_dir / f"{folder_name}_{chapter_dir.name}.json"
        if chapter_json.exists():
            # Read the chapter JSON to get page count
            try:
                with open(chapter_json, 'r', encoding='utf-8') as f:
                    chapter_data = json.load(f)
                master_index["chapters"].append({
                    "name": chapter_dir.name,
                    "json_file": f"{folder_name}_{chapter_dir.name}.json",
                    "total_pages": chapter_data.get("total_pages", 0)
                })
            except Exception as e:
                print(f"   ⚠️  Could not read chapter JSON for {chapter_dir.name}: {e}")
    
    # Write master index
    master_json = output_dir / f"{folder_name}.json"
    try:
        with open(master_json, 'w', encoding='utf-8') as f:
            json.dump(master_index, f, indent=2, ensure_ascii=False)
        print(f"✅ Created master index: {master_json}")
        return True
    except Exception as e:
        print(f"❌ Error writing master index: {e}")
        return False

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
            return process_with_ordered_detection(extract_folder, panel_result_dir)
        
    elif input_path.is_dir():
        # Check if this is a chapter-based directory
        if is_chapter_based_archive(input_path):
            print(f"📚 Detected chapter-based directory structure")
            return process_chapter_based_archive(input_path, panel_result_dir)
        else:
            # Process folder directly
            print(f"📁 Processing folder: {input_path}")
            return process_with_ordered_detection(input_path, panel_result_dir)
        
    else:
        print(f"❌ Unsupported input type: {input_path}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Process manga folders/archives with ordered_detection.py")
    parser.add_argument('input', help='Input folder or archive file')
    parser.add_argument('--pages-dir', default='Pages', help='Pages directory name')
    parser.add_argument('--output-dir', default='panel_result', help='Output directory name')
    
    args = parser.parse_args()
    
    print("🚀 Manga Processing Script Started")
    print("=" * 50)
    
    # Create directories
    pages_dir, panel_result_dir = create_directories()
    
    # Process input
    success = process_input(args.input, pages_dir, panel_result_dir)
    
    print("=" * 50)
    if success:
        print("🎉 Processing completed successfully!")
        print(f"📂 Results in: {panel_result_dir.absolute()}")
    else:
        print("❌ Processing failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
