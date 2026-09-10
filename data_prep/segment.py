"""ENTRY - run: python cli.py segment <brand>  (foundation stage 1: YOLO garment segmentation)."""

import os
import re
from PIL import Image, ImageCms
import numpy as np
from tqdm import tqdm
from ultralytics import YOLO
import torch
import torchvision.ops
import cv2
from collections import Counter, defaultdict
from psycopg2 import sql

from shared import db
from data_prep.heraldic_filter import HERALDIC_KEYWORDS
from data_prep.keyword_maps import BRAND_KEYWORD_MAPS

WRITE = True
DEST_TABLE = "segment_fpyolo11l241114"
SEARCH_DIR = os.environ.get("IMAGE_DIR", "./images")
# When SEG_OUTPUT_ROOT is set, segmentation PNGs land under
# <SEG_OUTPUT_ROOT>/<archive_folder_basename>/<MODEL_NAME>_<timestamp>/ instead of
# alongside the source JPEGs. Lets parity tests write to a sandbox tree while
# leaving the live image tree untouched. Unset = default (write next to JPEGs).
SEG_OUTPUT_ROOT = os.environ.get("SEG_OUTPUT_ROOT")
INITIAL_QUERY = """
    SELECT instance_id, archive_id_ref, title, title_second
    FROM instance
    WHERE archive_id_ref = ANY(%s);
"""
MODEL_NAME = "fpyolo11l241114"
# Weights live next to this module (data_prep/weights/); resolve relative to the
# file so it works regardless of the current working directory.
MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "weights", "Fashionpedia_YOLO11l_241114", "best.pt",
)
EXCLUDE_MERGE_CLASSES = {
    "skirt", "glasses", "hat", "headband", "hair covering", "hair accessory",
    "tie", "glove", "watch", "belt", "leg warmer", "tights", "stockings",
    "sock", "shoe", "bag", "wallet", "scarf", "umbrella", "pants", "shorts"
}
MIN_ATTRIBUTE_PIXELS = 50
IOU_THRESHOLD = 0.15
OVERLAP_RATIO_THRESHOLD = 0.1
ATTRIBUTE_KEYWORDS = {
    "sleeve", "pocket", "collar", "cuff", "hood",
    "button", "zipper", "lapel", "hem"
}
COUNTERS = {
    "total_images": 0,
    "filename_format_error": 0,
    "no_keyword_match": 0,
    "no_masks": 0,
    "no_garment_detection": 0,
    "filename_not_found": 0,
    "processed_successfully": 0,
    "exceptions": 0,
}

def compile_keyword_filter(keywords):
    pattern = r"\b(" + "|".join(keywords) + r")\b"
    return re.compile(pattern, re.IGNORECASE)

def get_keyword_match(row, keyword_filter):
    title = row.get("title") or ""
    title_second = row.get("title_second") or ""
    for field_name, field_value in [("title", title), ("title_second", title_second)]:
        match = keyword_filter.search(field_value)
        if match:
            return match.group(0), field_name
    return None

def filter_and_group_rows(rows, keyword_filter):
    raw_counts = Counter()
    grouped = defaultdict(list)
    excluded_rows = []

    for row in rows:
        aid = str(row["archive_id_ref"])
        raw_counts[aid] += 1

        match_info = get_keyword_match(row, keyword_filter)
        if match_info:
            matched_keyword, field_name = match_info
            excluded_rows.append((
                row["archive_id_ref"],
                row["instance_id"],
                row.get("title") or "",
                matched_keyword,
                field_name
            ))
            continue

        grouped[aid].append(row)

    return dict(raw_counts), dict(grouped), excluded_rows

def override_nms():
    def nms_cpu(boxes, scores, iou_thresh):
        return torch.ops.torchvision.nms(
            boxes.cpu(), scores.cpu(), iou_thresh
        )
    torchvision.ops.nms = nms_cpu

def load_model(path):
    model = YOLO(path)
    model.model.to("cuda")
    return model

def build_image_lookup(src_dir):
    lookup = {}
    for fname in os.listdir(src_dir):
        if fname.lower().endswith('.jpg'):
            name = os.path.splitext(fname)[0]
            parts = name.split("-")
            if len(parts) >= 2:
                lookup[(parts[0], parts[1])] = name
    return lookup

def compute_iou(box1, box2):
    x1 = max(box1[0], box2[0]); y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2]); y2 = min(box1[3], box2[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area1 = (box1[2]-box1[0])*(box1[3]-box1[1])
    area2 = (box2[2]-box2[0])*(box2[3]-box2[1])
    union = area1 + area2 - inter
    return inter/union if union > 0 else 0.0

def merge_masks_and_log(name, main_box, main_mask, r, garment_idxs, attr_idxs, row_logs):
    boxes = r.boxes.xyxy.cpu().numpy()
    masks = r.masks.data.cpu().numpy()
    combined = main_mask.copy()
    dilated = cv2.dilate(
        (main_mask>0.5).astype(np.uint8),
        np.ones((15,15),np.uint8), iterations=1
    )
    candidates = [i for i in garment_idxs if i != garment_idxs[0]] + attr_idxs
    total_iou = 0.0
    total_overlap = 0.0
    merge_count = 0
    for idx in candidates:
        other_box = boxes[idx]
        other_mask = (masks[idx] > 0.5).astype(np.uint8)
        area = other_mask.sum()
        if area < MIN_ATTRIBUTE_PIXELS:
            row_logs.append(f"{name}: Candidate idx {idx} skipped due to low pixel count ({area} pixels)")
            continue
        iou = compute_iou(main_box, other_box)
        overlap = cv2.bitwise_and(dilated, other_mask).sum() / area
        total_iou += iou
        total_overlap += overlap
        if iou > IOU_THRESHOLD or overlap > OVERLAP_RATIO_THRESHOLD:
            merge_count += 1
            combined = np.maximum(combined, masks[idx])
    count = len(candidates)
    avg_iou = total_iou / count if count > 0 else 0.0
    avg_overlap = total_overlap / count if count > 0 else 0.0
    row_logs.append(f"{name}: {count} candidate(s) evaluated, {merge_count} merged, Avg IoU: {avg_iou:.2f}, Avg Overlap: {avg_overlap:.2f}")
    return combined

_SRGB_PROFILE = ImageCms.createProfile("sRGB")


def _apply_icc_profile(img):
    """Convert image from its embedded ICC profile to sRGB. Returns RGB image."""
    icc_data = img.info.get("icc_profile")
    if not icc_data:
        return img.convert("RGB")
    try:
        src_profile = ImageCms.ImageCmsProfile(ImageCms.core.profile_frombytes(icc_data))
        return ImageCms.profileToProfile(
            img.convert("RGB"), src_profile, _SRGB_PROFILE,
            renderingIntent=ImageCms.Intent.PERCEPTUAL,
            outputMode="RGB",
        )
    except Exception:
        return img.convert("RGB")


def postprocess_and_save(image_path, mask, seg_dir, filename):
    img = _apply_icc_profile(Image.open(image_path)).convert("RGBA")
    w, h = img.size
    m = Image.fromarray((mask * 255).astype("uint8"), mode="L")
    m = m.resize((w, h), resample=Image.Resampling.NEAREST)
    eroded = cv2.erode(np.array(m), np.ones((10,10), np.uint8), iterations=1)
    composite = Image.new("RGBA", (w, h), (0,0,0,0))
    composite.paste(img, mask=Image.fromarray(eroded, mode="L"))
    composite.save(os.path.join(seg_dir, filename))

def insert_segment_and_get_id(cur, aid, iid, cls_id, cls_name, conf):
    query = sql.SQL("""
        INSERT INTO {table}
          (archive_id_ref, instance_id_ref, class_id, class_name, conf, is_hero)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING segment_id;
    """).format(table=sql.Identifier(DEST_TABLE))
    cur.execute(query, (aid, iid, cls_id, cls_name, conf, True))
    return cur.fetchone()[0]

def process_archive(aid, rows, keyword_match_column, keywordmap, src_dir, model, conn, cur, raw_count, excluded_rows):
    header = [
        f"Archive ID: {aid}",
        f"Total rows: {raw_count}",
        f"After filter: {len(rows)}"
    ]
    row_logs = []
    lookup = build_image_lookup(src_dir)
    if SEG_OUTPUT_ROOT:
        seg_parent = os.path.join(SEG_OUTPUT_ROOT, os.path.basename(os.path.normpath(src_dir)))
    else:
        seg_parent = src_dir
    seg_dir = db.create_subfolder_datetimesuffix(seg_parent, MODEL_NAME)

    for k in COUNTERS:
        COUNTERS[k] = 0

    for row in tqdm(rows, desc=f"Archive {aid}"):
        COUNTERS["total_images"] += 1
        iid = str(row["instance_id"])
        key = (aid, iid)
        name = lookup.get(key)
        if not name:
            COUNTERS["filename_not_found"] += 1
            row_logs.append(f"{iid}: file not found")
            continue

        try:
            r = model(os.path.join(src_dir, name + ".jpg"))[0]
            if r.masks is None:
                COUNTERS["no_masks"] += 1
                row_logs.append(f"{name}: no masks")
                continue

            texts = [row[col] or "" for col in keyword_match_column]
            words = set(re.findall(r"\b[\w-]+\b", " ".join(texts).lower()))
            matches = [k for k in keywordmap if k.lower() in words]
            if not matches:
                COUNTERS["no_keyword_match"] += 1
                row_logs.append(f"{name}: no keyword match")
                continue
            synonyms = {s.strip().lower() for k in matches for s in keywordmap[k].split(",")}

            classes = [model.names[int(c)] for c in r.boxes.cls]
            garment_idxs = []
            attr_idxs = []
            for i, c in enumerate(classes):
                parts = re.findall(r"\b[\w-]+\b", c.lower())
                if any(p in synonyms for p in parts):
                    garment_idxs.append(i)
                elif any(p in ATTRIBUTE_KEYWORDS for p in parts):
                    attr_idxs.append(i)

            if not garment_idxs:
                COUNTERS["no_garment_detection"] += 1
                row_logs.append(f"{name}: no garment detection")
                continue

            boxes = r.boxes.xyxy.cpu().numpy()
            main_idx = max(garment_idxs, key=lambda i: (boxes[i][2]-boxes[i][0]) * (boxes[i][3]-boxes[i][1]))
            main_box = boxes[main_idx]
            main_mask = r.masks.data[main_idx].cpu().numpy()

            if classes[main_idx].lower() not in EXCLUDE_MERGE_CLASSES:
                combined = merge_masks_and_log(name, main_box, main_mask, r, garment_idxs, attr_idxs, row_logs)
            else:
                row_logs.append(f"{name}: excluded class {classes[main_idx]}")
                combined = main_mask

            seg_id = insert_segment_and_get_id(
                cur, aid, iid,
                int(r.boxes.cls[main_idx]), classes[main_idx], float(r.boxes.conf[main_idx])
            )
            if WRITE:
                parts = name.split("-")
                if len(parts) < 5:
                    COUNTERS["filename_format_error"] += 1
                    row_logs.append(f"{name}: bad filename format")
                    continue
                fam, rank, pid = parts[2:5]
                fn = f"{aid}-{iid}-{fam}-{rank}-{pid}-{MODEL_NAME}-{seg_id}.png"
                postprocess_and_save(os.path.join(src_dir, name + ".jpg"), combined, seg_dir, fn)

            COUNTERS["processed_successfully"] += 1
            row_logs.append(f"{name}: success (seg_id {seg_id})")

        except Exception as e:
            COUNTERS["exceptions"] += 1
            row_logs.append(f"{name}: exception {e}")

    summary_lines = []
    summary_lines += [
        "----- Summary -----",
        *(f"{k}: {v}" for k, v in COUNTERS.items()),
        "---"
    ]
    if excluded_rows:
        summary_lines.append("Excluded products due to unwanted keywords:")
        for aid2, iid2, title2, kw, field in excluded_rows:
            summary_lines.append(
                f"'{kw}' matched in {field} → Title {title2}, Archive {aid2}, Instance {iid2}"
            )
    else:
        summary_lines.append("No products were excluded due to keyword filtering.")
    final_log = header + [""] + summary_lines + [""] + row_logs
    write_log(final_log, seg_dir)

def write_log(log_lines, seg_dir):
    os.makedirs(seg_dir, exist_ok=True)
    with open(os.path.join(seg_dir, "processing_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

def run_segmentation(brand: str, keyword_match_column=None, keywordmap=None):
    """
    Segment garments from product images using YOLO model.

    Args:
        brand: Brand name (e.g., 'nike', 'adidas')
        keyword_match_column: List of columns to search for keywords (default: ['title', 'title_second'])
        keywordmap: Dict mapping product types to YOLO class synonyms
    """
    # Default keyword matching configuration
    if keyword_match_column is None:
        keyword_match_column = ['title', 'title_second']
    if keywordmap is None:
        keywordmap = BRAND_KEYWORD_MAPS.get(brand, {})

    keyword_filter = compile_keyword_filter(HERALDIC_KEYWORDS)
    archive_dir_map = db.map_archive_dirs(brand, SEARCH_DIR)
    conn, cur = db.connect_to_db(brand, readonly=False)
    unprocessed = db.get_unprocessed_archives(cur, DEST_TABLE)
    cur.execute(INITIAL_QUERY, (unprocessed,))
    rows = cur.fetchall()
    raw_counts, grouped, excluded_rows = filter_and_group_rows(rows, keyword_filter)

    override_nms()
    model = load_model(MODEL_PATH)

    for aid, rows in grouped.items():
        src_dir = archive_dir_map.get(aid)
        if not src_dir:
            print(f"ERROR: No folder found for archive_id_ref {aid}.")
            continue
        process_archive(aid, rows, keyword_match_column, keywordmap, src_dir, model, conn, cur, raw_counts[aid], excluded_rows)
        # Per-archive commit for fault tolerance — matches old-code contract
        # (CLAUDE.md: "commit per archive for fault tolerance"). A mid-run
        # crash keeps completed archives instead of rolling back the whole
        # brand.
        if WRITE:
            conn.commit()
        else:
            conn.rollback()

    cur.close()
    conn.close()