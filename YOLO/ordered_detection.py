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

def merge_boxes(box1, box2):
    """Returns a new box that encompasses both input boxes."""
    return [
        min(box1[0], box2[0]), # Smallest X1
        min(box1[1], box2[1]), # Smallest Y1
        max(box1[2], box2[2]), # Largest X2
        max(box1[3], box2[3])  # Largest Y3
    ]

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

def xy_cut_sort(boxes, rtl=False):
    """
    Recursive XY-Cut sorting for comic panels.
    boxes: List of [x1, y1, x2, y2]
    rtl: True for Manga, False for Western comics
    """
    if len(boxes) <= 1:
        return boxes

    # 1. Try a Horizontal Cut (Find Rows)
    boxes.sort(key=lambda b: b[1]) # Sort by Y1
    clusters = []
    current_cluster = [boxes[0]]
    
    for i in range(1, len(boxes)):
        # If this box starts below the previous box's bottom, it's a new row
        if boxes[i][1] > current_cluster[-1][3] * 0.95: 
            clusters.append(current_cluster)
            current_cluster = [boxes[i]]
        else:
            current_cluster.append(boxes[i])
    clusters.append(current_cluster)

    # 2. Try Vertical Cuts within each row
    sorted_panels = []
    for cluster in clusters:
        # Sort each row by X1 (Western = Left to Right, Manga = Right to Left)
        cluster.sort(key=lambda b: b[0], reverse=rtl)
        sorted_panels.extend(cluster)
        
    return sorted_panels

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
            print("No boxes detected. Try lowering the confidence threshold.")
            # Save empty result
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

        # 4. Apply Kumiko sorting to the cleaned boxes
        start_time = time.time()
        ordered_boxes = xy_cut_sort(cleaned_boxes, rtl=True)
        sort_time = time.time() - start_time
        print(f"Sorted boxes in {sort_time:.2f} seconds")

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
            ink_coords = cv2.findNonZero(ink_mask)
            
            if ink_coords is not None:
                ix, iy, iw, ih = cv2.boundingRect(ink_coords)
                final_output_boxes.append([x1 + ix, y1 + iy, x1 + ix + iw, y1 + iy + ih])
            else:
                final_output_boxes.append([x1, y1, x2, y2])
        
        shrink_time = time.time() - start_time
        print(f"Shrink-wrapped {len(final_output_boxes)} panels in {shrink_time:.2f} seconds")

        # Save result with the correct reading order and shrink-wrapped boxes
        output = {"reading_order": []}
        for i, box in enumerate(final_output_boxes):
            output["reading_order"].append({
                "index": i + 1,
                "bbox": [int(c) for c in box]
            })

        with open("reading_order.json", "w") as f:
            json.dump(output, f, indent=4)

        total_time = load_time + inference_time + merge_time + sort_time + shrink_time
        print(f"✅ Detected {len(final_output_boxes)} panels in correct reading order.")
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
