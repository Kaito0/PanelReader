import json
import torch
import numpy as np
import os
import sys
import argparse
import time
import signal
import cv2
from ultralytics import YOLO
from huggingface_hub import hf_hub_download

def get_model():
    """Downloads model if not present locally, then returns YOLO instance."""
    model_name = "v2023.12.07_l_yv11/model.pt"
    local_dir = "./models" # You can change this to any persistent directory
    local_path = os.path.join(local_dir, model_name)

    if not os.path.exists(local_path):
        print("Model not found locally. Downloading from Hugging Face...")
        os.makedirs(local_dir, exist_ok=True)
        # This downloads to local_dir and returns the path
        path = hf_hub_download(
            repo_id="deepghs/manga109_yolo", 
            filename=model_name,
            local_dir=local_dir
        )
    else:
        print(f"Loading model from local path: {local_path}")
        path = local_path

    return YOLO(path).to("cuda" if torch.cuda.is_available() else "cpu")

def build_panel_dag(boxes):
    """
    Build a Directed Acyclic Graph (DAG) for panel ordering.
    Panel A comes before B if:
    - A.y2 < B.y1 (A is above B) OR
    - A and B are in same row AND A.x1 > B.x2 (RTL order)
    Returns adjacency list and topological order.
    """
    n = len(boxes)
    if n <= 1:
        return list(range(n)), {}
    
    try:
        # Build adjacency list
        adj = {i: [] for i in range(n)}
        in_degree = {i: 0 for i in range(n)}
        
        # Determine which boxes are in the same row
        same_row = {}
        for i in range(n):
            same_row[i] = []
            for j in range(n):
                if i != j:
                    # Check if boxes are in the same row (significant Y overlap)
                    y_overlap = min(boxes[i][3], boxes[j][3]) - max(boxes[i][1], boxes[j][1])
                    min_height = min(boxes[i][3] - boxes[i][1], boxes[j][3] - boxes[j][1])
                    if y_overlap > min_height * 0.3:  # 30% Y overlap means same row
                        same_row[i].append(j)
        
        # Build edges based on rules
        for i in range(n):
            for j in range(n):
                if i != j:
                    box_i, box_j = boxes[i], boxes[j]
                    
                    # Rule 1: A is above B (A.y2 < B.y1)
                    if box_i[3] < box_j[1]:
                        adj[i].append(j)
                        in_degree[j] += 1
                    
                    # Rule 2: Same row and RTL order (A.x1 > B.x2)
                    elif j in same_row[i] and box_i[0] > box_j[2]:
                        adj[i].append(j)
                        in_degree[j] += 1
        
        # Topological sort using Kahn's algorithm
        queue = [i for i in range(n) if in_degree[i] == 0]
        topo_order = []
        
        while queue:
            if len(queue) > 1:
                # If multiple nodes have no dependencies, sort by Y position (top to bottom)
                queue.sort(key=lambda i: boxes[i][1])
            
            u = queue.pop(0)
            topo_order.append(u)
            
            for v in adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        
        # Fallback: if topological sort failed, return natural order
        if len(topo_order) != n:
            print("Warning: Topological sort incomplete, using natural order")
            return list(range(n)), adj
            
        return topo_order, adj
        
    except Exception as e:
        print(f"Warning: DAG building failed: {e}")
        print("Using natural order as fallback")
        return list(range(n)), {}

def detect_gutters_and_refine_boxes(boxes, gray_image):
    """
    Enhanced gutter detection using Hough Transform for RTL manga.
    Detects white gutters between panels and refines box boundaries.
    """
    if len(boxes) <= 1:
        return boxes
    
    try:
        refined_boxes = []
        height, width = gray_image.shape
        
        # Create binary image for gutter detection (white areas)
        _, gutter_mask = cv2.threshold(gray_image, 240, 255, cv2.THRESH_BINARY)
        
        # Detect vertical lines (gutters between columns) - enhanced for RTL
        vertical_lines = cv2.HoughLinesP(
            255 - gutter_mask,  # Invert to detect white lines as dark lines
            rho=1,
            theta=np.pi/180,
            threshold=30,  # Lower threshold for more sensitive detection
            minLineLength=max(width // 15, 30),
            maxLineGap=20
        )
        
        # Detect horizontal lines (gutters between rows)
        horizontal_lines = cv2.HoughLinesP(
            255 - gutter_mask,
            rho=1,
            theta=np.pi/180,
            threshold=30,
            minLineLength=max(height // 15, 30),
            maxLineGap=20
        )
        
        # Group and cluster lines by position
        vertical_gutters = []
        horizontal_gutters = []
        
        if vertical_lines is not None:
            for line in vertical_lines:
                x1, y1, x2, y2 = line[0]
                # Check if it's mostly vertical
                if abs(x2 - x1) < 15:  # Nearly vertical
                    avg_x = (x1 + x2) / 2
                    vertical_gutters.append(avg_x)
        
        if horizontal_lines is not None:
            for line in horizontal_lines:
                x1, y1, x2, y2 = line[0]
                # Check if it's mostly horizontal
                if abs(y2 - y1) < 15:  # Nearly horizontal
                    avg_y = (y1 + y2) / 2
                    horizontal_gutters.append(avg_y)
        
        # Cluster similar gutter positions
        if vertical_gutters:
            vertical_gutters = sorted(list(set(int(g) for g in vertical_gutters)))
        if horizontal_gutters:
            horizontal_gutters = sorted(list(set(int(g) for g in horizontal_gutters)))
        
        # Refine each box using detected gutters
        for box in boxes:
            x1, y1, x2, y2 = box
            refined_box = box.copy()
            
            # For RTL: prioritize right gutters first, then left gutters
            # Find nearest right gutter (RTL: panels end at right gutter)
            right_gutters = [g for g in vertical_gutters if g > x2 and abs(g - x2) < width // 15]
            if right_gutters:
                refined_box[2] = min(right_gutters[0] - 1, refined_box[2])  # Move left to gutter
            
            # Find nearest left gutter (RTL: panels start at left gutter)
            left_gutters = [g for g in vertical_gutters if g < x1 and abs(g - x1) < width // 15]
            if left_gutters:
                refined_box[0] = max(left_gutters[-1] + 1, refined_box[0])  # Move right to gutter
            
            # Find nearest top gutter
            top_gutters = [g for g in horizontal_gutters if g < y1 and abs(g - y1) < height // 15]
            if top_gutters:
                refined_box[1] = max(top_gutters[-1] + 1, refined_box[1])  # Move down to gutter
            
            # Find nearest bottom gutter
            bottom_gutters = [g for g in horizontal_gutters if g > y2 and abs(g - y2) < height // 15]
            if bottom_gutters:
                refined_box[3] = min(bottom_gutters[0] - 1, refined_box[3])  # Move up to gutter
            
            refined_boxes.append(refined_box)
        
        return refined_boxes
        
    except Exception as e:
        print(f"Warning: Gutter detection failed: {e}")
        print("Using original boxes as fallback")
        return boxes

def merge_boxes(box1, box2):
    """Returns a new box that encompasses both input boxes."""
    return [
        min(box1[0], box2[0]), # Smallest X1
        min(box1[1], box2[1]), # Smallest Y1
        max(box1[2], box2[2]), # Largest X2
        max(box1[3], box2[3])  # Largest Y3
    ]

def calculate_iou(box1, box2):
    """Calculate Intersection over Union of two boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    
    if x2 <= x1 or y2 <= y1:
        return 0.0
    
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - intersection
    
    return intersection / union if union > 0 else 0.0

def check_containment(box1, box2, threshold=0.9):
    """Check if one box is contained within another by threshold percentage."""
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    
    # Calculate intersection area
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    
    if x2 <= x1 or y2 <= y1:
        return False
    
    intersection = (x2 - x1) * (y2 - y1)
    
    # Check if intersection is at least threshold% of either box
    return (intersection / area1 >= threshold) or (intersection / area2 >= threshold)

def merge_overlapping_boxes(boxes, overlap_threshold=0.3):
    """
    Merge boxes that overlap by more than threshold and are on same Y-axis.
    boxes: List of [x1, y1, x2, y2]
    overlap_threshold: Minimum overlap ratio to merge (0.3 = 30%)
    """
    if len(boxes) <= 1:
        return boxes
    
    boxes = boxes.copy()
    merged = True
    
    while merged:
        merged = False
        i = 0
        
        while i < len(boxes):
            j = i + 1
            while j < len(boxes):
                box1, box2 = boxes[i], boxes[j]
                
                # Check containment first - if one box is 90% inside another, merge regardless
                if check_containment(box1, box2):
                    boxes[i] = merge_boxes(box1, box2)
                    boxes.pop(j)
                    merged = True
                    break
                
                # Check if boxes are on same Y-axis (row)
                y_overlap = min(box1[3], box2[3]) - max(box1[1], box2[1])
                min_height = min(box1[3] - box1[1], box2[3] - box2[1])
                same_row = y_overlap > min_height * 0.5  # 50% Y overlap
                
                if same_row:
                    # Calculate overlap area
                    x_overlap = max(0, min(box1[2], box2[2]) - max(box1[0], box2[0]))
                    overlap_area = x_overlap * y_overlap
                    
                    # Calculate individual areas
                    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
                    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
                    
                    # Check overlap ratio
                    overlap_ratio = overlap_area / min(area1, area2)
                    
                    if overlap_ratio > overlap_threshold:
                        # Merge boxes
                        boxes[i] = merge_boxes(box1, box2)
                        boxes.pop(j)
                        merged = True
                        break
                j += 1
            if merged:
                break
            i += 1
    
    return boxes



# --- Execution ---
def main():
    parser = argparse.ArgumentParser(description="Detect and order comic panels")
    parser.add_argument('-i', '--input', required=True, help='Input image path')
    parser.add_argument('--timeout', type=int, default=300, help='Processing timeout in seconds')
    args = parser.parse_args()
    
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Processing timed out after {args.timeout} seconds")
    
    # Set timeout signal
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(args.timeout)
    
    try:
        img_path = args.input
        if not os.path.exists(img_path):
            print(f"Error: Image path not found: {img_path}")
            sys.exit(1)

        print("Loading mosesb model...")
        start_time = time.time()
        model = get_model()
        load_time = time.time() - start_time
        print(f"Model loaded in {load_time:.2f} seconds")

        print(f"Processing image: {img_path}")
        print(f"Image size: {os.path.getsize(img_path)} bytes")
        
        # Check if CUDA is available
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Using device: {device}")
        
        # Process with timing
        start_time = time.time()
        results = model(img_path, imgsz=640, conf=0.25, iou=0.6)[0]
        inference_time = time.time() - start_time
        print(f"Inference completed in {inference_time:.2f} seconds")

        # 2. Extract raw boxes
        raw_boxes = results.boxes.xyxy.cpu().numpy().tolist()
        print(f"Found {len(raw_boxes)} raw boxes")

        if len(raw_boxes) == 0:
            print("No boxes detected. Creating single panel covering entire image.")
            
            # Get image dimensions
            try:
                img = cv2.imread(img_path)
                if img is not None:
                    height, width = img.shape[:2]
                    # Create a single panel covering the entire image with small margin
                    margin = 5
                    full_image_box = [margin, margin, width - margin, height - margin]
                    
                    output = {"reading_order": [{
                        "index": 1,
                        "bbox": [int(c) for c in full_image_box]
                    }]}
                    
                    with open("reading_order.json", "w") as f:
                        json.dump(output, f, indent=4)
                    print(f"Created single panel covering entire image: {width}x{height}")
                    print("Saved result to reading_order.json")
                    return
                else:
                    print("Could not read image dimensions")
                    # Fallback to empty result
                    output = {"reading_order": []}
                    with open("reading_order.json", "w") as f:
                        json.dump(output, f, indent=4)
                    print("Saved empty result to reading_order.json")
                    return
            except Exception as e:
                print(f"Error creating full image panel: {e}")
                # Fallback to empty result
                output = {"reading_order": []}
                with open("reading_order.json", "w") as f:
                    json.dump(output, f, indent=4)
                print("Saved empty result to reading_order.json")
                return

        # 3. Merge overlapping boxes on same row
        # Adjust overlap_threshold (0.3 = 30%) as needed
        start_time = time.time()
        cleaned_boxes = merge_overlapping_boxes(raw_boxes, overlap_threshold=0.3)
        merge_time = time.time() - start_time
        print(f"Merged boxes in {merge_time:.2f} seconds: {len(cleaned_boxes)} boxes")

        # 4. Apply DAG-based panel ordering with RTL logic
        start_time = time.time()
        topo_order, adj = build_panel_dag(cleaned_boxes)
        ordered_boxes = [cleaned_boxes[i] for i in topo_order]
        sort_time = time.time() - start_time
        print(f"DAG-based RTL ordering completed in {sort_time:.2f} seconds")
        print(f"Panel order: {[i+1 for i in topo_order]}")

        # Optional: Shrink-wrap the final boxes to the actual ink inside them
        print("Shrink-wrapping panels to actual content...")
        start_time = time.time()
        
        # Load the full image for shrink-wrapping
        full_img = cv2.imread(img_path)
        full_img_gray = cv2.cvtColor(full_img, cv2.COLOR_BGR2GRAY)
        
        final_output_boxes = []
        for box in ordered_boxes:
            x1, y1, x2, y2 = [int(c) for c in box]
            # Crop to the detected panel
            panel_roi = full_img_gray[y1:y2, x1:x2]
            # Find the ink (anything not white)
            _, ink_mask = cv2.threshold(panel_roi, 245, 255, cv2.THRESH_BINARY_INV)
            
            # Apply morphological closing to connect fragmented ink lines
            # Create a kernel for morphological operations (adjust size based on image resolution)
            kernel_size = max(3, min((x2 - x1) // 50, (y2 - y1) // 50, 7))  # Adaptive kernel size
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
            
            # Closing: Dilation followed by Erosion
            ink_mask_closed = cv2.morphologyEx(ink_mask, cv2.MORPH_CLOSE, kernel)
            
            ink_coords = cv2.findNonZero(ink_mask_closed)
            
            if ink_coords is not None:
                ix, iy, iw, ih = cv2.boundingRect(ink_coords)
                final_output_boxes.append([x1 + ix, y1 + iy, x1 + ix + iw, y1 + iy + ih])
            else:
                final_output_boxes.append([x1, y1, x2, y2])
        
        shrink_time = time.time() - start_time
        print(f"Shrink-wrapped {len(final_output_boxes)} panels in {shrink_time:.2f} seconds")

        # 5. Refine panel boundaries using gutter detection
        print("Detecting gutters and refining panel boundaries...")
        start_time = time.time()
        try:
            refined_boxes = detect_gutters_and_refine_boxes(final_output_boxes, full_img_gray)
            gutter_time = time.time() - start_time
            print(f"Refined panel boundaries using gutters in {gutter_time:.2f} seconds")
        except Exception as e:
            print(f"Warning: Gutter detection failed: {e}")
            print("Using shrink-wrapped boxes as fallback")
            refined_boxes = final_output_boxes
            gutter_time = time.time() - start_time

        # Ensure we always save the final result, even with single panel
        print("Saving final panel detection results...")
        output = {"reading_order": []}
        for i, box in enumerate(refined_boxes):
            output["reading_order"].append({
                "index": i + 1,
                "bbox": [int(c) for c in box]
            })

        try:
            with open("reading_order.json", "w") as f:
                json.dump(output, f, indent=4)
            print(f"✅ Successfully saved {len(refined_boxes)} panels to reading_order.json")
        except Exception as e:
            print(f"❌ Error saving results: {e}")
            sys.exit(1)

        total_time = load_time + inference_time + merge_time + sort_time + shrink_time + gutter_time
        print(f"✅ Detected {len(refined_boxes)} panels in correct reading order.")
        print(f"Total processing time: {total_time:.2f} seconds")
        
        # Cancel timeout
        signal.alarm(0)
        
    except TimeoutError as e:
        print(f"❌ {e}")
        print("Try:")
        print("  1. Using a smaller image")
        print("  2. Increasing timeout with --timeout 600")
        print("  3. Checking if the image is corrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error during processing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
