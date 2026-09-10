"""
ENTRY - run: python cli.py archetype-validation  (builds the validation tool -> archetype_validation.html).

Validation HTML Generator (Production Version)
================================================

Generates a self-contained HTML validation tool from the JSON sample file.
Enhanced with HSB saturation metadata, coverage_1 display, has_saturated
status, AND an in-tool merge-group labeling panel (per-cluster grouping that
can be corrected by hand and exported for merge-rule recalibration).

Most workflows reach this through `archetype-review`, which classifies (if
needed), builds the stratified sample, and renders this tool in one idempotent
command. `archetype-validation` runs only this render step against an existing
sample JSON.

Programmatic API: build_validator_html(input_json, output_dir) -> output_path
(used by archetype-review).

This script is READ-ONLY:
- Only reads validation_sample.json
- Creates NEW HTML file (no modifications to existing files)
- Validation results are stored in browser localStorage (not filesystem)
- User manually exports results via download button

Usage:
    docker compose run --rm pipeline python cli.py archetype-review
    docker compose run --rm pipeline python cli.py archetype-validation

    # After running, open the generated HTML in a browser
"""

import os
import sys
import json
import argparse
from datetime import datetime

from shared.palette_merge.palette_merge import (
    LOW_CHROMA_MERGE_THRESHOLD,
    NEUTRAL_CHROMA_THRESHOLD,
    LOW_CHROMA_MAX_DELTA_L,
    HUE_MERGE_THRESHOLD,
)

# Paths
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_INPUT_JSON = os.path.join(_SCRIPT_DIR, 'outputs', 'validation_sample.json')
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs')

# Legacy localStorage key (pre-v2, keyed by sample_id). Kept for migration only.
LEGACY_STORAGE_KEY = 'archetype_validation_results_hsb_production'


# ============================================================================
# HTML TEMPLATE
# ============================================================================

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Archetype Classification Validation (HSB Production)</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            min-height: 100vh;
        }}

        /* Header */
        header {{
            background: #222;
            padding: 15px 20px;
            border-bottom: 1px solid #333;
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}

        h1 {{
            font-size: 20px;
            color: #3B82F6;
        }}

        .progress-container {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}

        .progress-text {{
            font-size: 14px;
            color: #9CA3AF;
        }}

        .progress-text strong {{
            color: #22C55E;
        }}

        .progress-bar {{
            width: 200px;
            height: 8px;
            background: #333;
            border-radius: 4px;
            overflow: hidden;
        }}

        .progress-fill {{
            height: 100%;
            background: #22C55E;
            transition: width 0.3s ease;
        }}

        /* Navigation */
        nav {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        button {{
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s;
        }}

        button:hover {{
            transform: translateY(-1px);
        }}

        button:active {{
            transform: translateY(0);
        }}

        .btn-primary {{
            background: #3B82F6;
            color: white;
        }}

        .btn-primary:hover {{
            background: #2563EB;
        }}

        .btn-secondary {{
            background: #374151;
            color: #e0e0e0;
        }}

        .btn-secondary:hover {{
            background: #4B5563;
        }}

        .btn-success {{
            background: #22C55E;
            color: white;
        }}

        .btn-success:hover {{
            background: #16A34A;
        }}

        .btn-warning {{
            background: #F97316;
            color: white;
        }}

        .btn-warning:hover {{
            background: #EA580C;
        }}

        select {{
            padding: 8px 12px;
            border: 1px solid #444;
            border-radius: 6px;
            background: #2a2a2a;
            color: #e0e0e0;
            font-size: 13px;
            min-width: 150px;
        }}

        /* Main content */
        main {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }}

        .product-card {{
            background: #222;
            border-radius: 12px;
            overflow: hidden;
        }}

        .product-header {{
            padding: 15px 20px;
            background: #2a2a2a;
            border-bottom: 1px solid #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .product-info {{
            display: flex;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }}

        .brand-tag {{
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}

        .brand-nike {{ background: #F97316; color: white; }}
        .brand-adidas {{ background: #3B82F6; color: white; }}
        .brand-puma {{ background: #22C55E; color: white; }}
        .brand-lulu {{ background: #EC4899; color: white; }}
        .brand-ua {{ background: #6B7280; color: white; }}

        .instance-id {{
            font-size: 12px;
            color: #666;
        }}

        .product-title {{
            font-size: 14px;
            color: #9CA3AF;
            max-width: 400px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .current-classification {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .archetype-badge {{
            padding: 6px 12px;
            background: #3B82F6;
            color: white;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
        }}

        /* HSB metadata badges */
        .meta-badges {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .meta-badge {{
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }}

        .badge-saturated {{
            background: #7C3AED;
            color: white;
        }}

        .badge-not-saturated {{
            background: #4B5563;
            color: #9CA3AF;
        }}

        .badge-coverage {{
            background: #1E3A5F;
            color: #93C5FD;
        }}

        .product-body {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            padding: 20px;
        }}

        @media (max-width: 1200px) {{
            .product-body {{
                grid-template-columns: 1fr 1fr;
            }}
        }}

        @media (max-width: 800px) {{
            .product-body {{
                grid-template-columns: 1fr;
            }}
        }}

        /* Images section */
        .images-section {{
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}

        .image-pair {{
            display: flex;
            gap: 10px;
        }}

        .image-container {{
            flex: 1;
            background: #333;
            border-radius: 8px;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 200px;
            position: relative;
        }}

        .image-container img {{
            max-width: 100%;
            max-height: 300px;
            object-fit: contain;
        }}

        .image-container.segmented {{
            background: #2a2a2a;
        }}

        .image-label {{
            position: absolute;
            bottom: 5px;
            left: 5px;
            background: rgba(0,0,0,0.7);
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 10px;
            color: #9CA3AF;
        }}

        /* Clusters section */
        .clusters-section {{
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}

        .cluster-group {{
            background: #2a2a2a;
            border-radius: 8px;
            padding: 15px;
        }}

        .cluster-group h3 {{
            font-size: 12px;
            color: #9CA3AF;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .color-bar {{
            display: flex;
            height: 24px;
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 10px;
        }}

        .color-swatch {{
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 30px;
            font-size: 10px;
            font-weight: 600;
            color: white;
            text-shadow: 0 0 3px rgba(0,0,0,0.8);
        }}

        .cluster-details {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .cluster-row {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .cluster-mini-swatch {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
            flex-shrink: 0;
        }}

        .cluster-label {{
            font-size: 10px;
            color: #888;
            font-family: 'Monaco', 'Menlo', monospace;
        }}

        .hsb-badge {{
            display: inline-block;
            padding: 1px 4px;
            border-radius: 3px;
            font-size: 9px;
            font-weight: 600;
            margin-left: 4px;
        }}

        .hsb-S {{ background: #7C3AED; color: white; }}
        .hsb-M {{ background: #D97706; color: white; }}
        .hsb-N {{ background: #4B5563; color: #ccc; }}

        /* Validation form */
        .validation-section {{
            background: #2a2a2a;
            border-radius: 8px;
            padding: 20px;
        }}

        .validation-section h3 {{
            font-size: 14px;
            color: #e0e0e0;
            margin-bottom: 15px;
        }}

        .radio-group {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 15px;
        }}

        .radio-option {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            background: #333;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .radio-option:hover {{
            background: #3a3a3a;
        }}

        .radio-option.selected {{
            background: #1E40AF;
            border: 1px solid #3B82F6;
        }}

        .radio-option.correct.selected {{
            background: #166534;
            border: 1px solid #22C55E;
        }}

        .radio-option.uncertain.selected {{
            background: #854D0E;
            border: 1px solid #EAB308;
        }}

        .radio-option input {{
            display: none;
        }}

        .radio-option .radio-circle {{
            width: 18px;
            height: 18px;
            border: 2px solid #666;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .radio-option.selected .radio-circle {{
            border-color: #3B82F6;
        }}

        .radio-option.selected .radio-circle::after {{
            content: '';
            width: 10px;
            height: 10px;
            background: #3B82F6;
            border-radius: 50%;
        }}

        .radio-option.correct.selected .radio-circle {{
            border-color: #22C55E;
        }}

        .radio-option.correct.selected .radio-circle::after {{
            background: #22C55E;
        }}

        .radio-label {{
            font-size: 13px;
        }}

        .form-group {{
            margin-bottom: 15px;
        }}

        .form-group label {{
            display: block;
            font-size: 12px;
            color: #9CA3AF;
            margin-bottom: 6px;
        }}

        .confidence-group {{
            display: flex;
            gap: 10px;
        }}

        .confidence-btn {{
            flex: 1;
            padding: 8px;
            background: #333;
            border: 1px solid #444;
            border-radius: 6px;
            color: #9CA3AF;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .confidence-btn:hover {{
            background: #3a3a3a;
        }}

        .confidence-btn.selected {{
            background: #374151;
            border-color: #3B82F6;
            color: #e0e0e0;
        }}

        textarea {{
            width: 100%;
            padding: 10px;
            background: #333;
            border: 1px solid #444;
            border-radius: 6px;
            color: #e0e0e0;
            font-size: 13px;
            resize: vertical;
            min-height: 60px;
        }}

        textarea:focus {{
            outline: none;
            border-color: #3B82F6;
        }}

        .action-buttons {{
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }}

        .action-buttons button {{
            flex: 1;
        }}

        /* Footer */
        footer {{
            background: #222;
            padding: 15px 20px;
            border-top: 1px solid #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .export-buttons {{
            display: flex;
            gap: 10px;
        }}

        .last-saved {{
            font-size: 12px;
            color: #666;
        }}

        /* Status indicator */
        .validation-status {{
            position: absolute;
            top: 10px;
            right: 10px;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
        }}

        .status-validated {{
            background: #22C55E;
            color: white;
        }}

        .status-pending {{
            background: #EAB308;
            color: black;
        }}

        /* Keyboard shortcuts hint */
        .shortcuts-hint {{
            font-size: 11px;
            color: #666;
            margin-top: 10px;
        }}

        .shortcuts-hint kbd {{
            background: #333;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }}

        /* Merge labeling panel */
        .merge-panel {{
            background: #2a2a2a;
            border-radius: 8px;
            padding: 15px;
            margin-top: 0;
        }}

        .merge-panel-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}

        .merge-panel-header h3 {{
            font-size: 12px;
            color: #9CA3AF;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 0;
        }}

        .merge-status {{
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
        }}

        .merge-status-modified {{
            background: #F97316;
            color: white;
        }}

        .merge-status-matches {{
            background: #4B5563;
            color: #9CA3AF;
        }}

        .merge-groups-container {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 12px;
        }}

        .merge-group-row {{
            display: flex;
            align-items: center;
            gap: 8px;
            min-height: 32px;
        }}

        .merge-group-badge {{
            width: 24px;
            height: 24px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            color: white;
            flex-shrink: 0;
        }}

        .merge-group-swatches {{
            display: flex;
            gap: 4px;
            flex-wrap: wrap;
        }}

        .merge-cluster-swatch {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
            border: 2px solid transparent;
            min-width: 60px;
            text-align: center;
            line-height: 1.3;
        }}

        .merge-cluster-swatch:hover {{
            transform: translateY(-2px);
            border-color: rgba(255,255,255,0.4);
        }}

        .merge-action-buttons {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .merge-action-buttons button {{
            padding: 6px 12px;
            font-size: 11px;
        }}

        /* Merge progress in header */
        .merge-progress-text {{
            font-size: 14px;
            color: #9CA3AF;
        }}

        .merge-progress-text strong {{
            color: #F97316;
        }}
    </style>
</head>
<body>
    <header>
        <div class="header-top">
            <h1>Archetype Classification Validation (HSB Production)</h1>
            <div class="progress-container">
                <span class="progress-text">
                    Archetype: <strong id="validated-count">0</strong> / <span id="total-count">0</span>
                </span>
                <div class="progress-bar">
                    <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
                </div>
                <span class="merge-progress-text">
                    Merge: <strong id="merge-labeled-count">0</strong> / <span id="merge-total-count">0</span>
                </span>
                <div class="progress-bar">
                    <div class="progress-fill" id="merge-progress-fill" style="width: 0%; background: #F97316;"></div>
                </div>
            </div>
        </div>
        <nav>
            <button class="btn-secondary" onclick="prevProduct()">&larr; Previous</button>
            <button class="btn-primary" onclick="nextProduct()">Next &rarr;</button>
            <button class="btn-warning" onclick="jumpToUnvalidated()">Next Unvalidated</button>
            <select id="product-select" onchange="jumpToIndex(this.value)">
                <!-- Populated by JavaScript -->
            </select>
            <span style="color: #666; font-size: 12px; margin-left: 10px;">
                Product <strong id="current-index">1</strong>
            </span>
        </nav>
    </header>

    <main>
        <div class="product-card" id="product-card">
            <div class="product-header">
                <div class="product-info">
                    <span class="brand-tag" id="brand-tag">Nike</span>
                    <span class="instance-id" id="instance-id">id:12345</span>
                    <span class="product-title" id="product-title">Product Title</span>
                </div>
                <div class="meta-badges" id="meta-badges">
                    <!-- HSB/coverage badges populated by JS -->
                </div>
                <div class="current-classification">
                    <span style="font-size: 12px; color: #666;">Current:</span>
                    <span class="archetype-badge" id="archetype-badge">MONO</span>
                </div>
            </div>

            <div class="product-body">
                <!-- Images -->
                <div class="images-section">
                    <div class="image-pair">
                        <div class="image-container">
                            <img id="original-image" src="" alt="Original">
                            <span class="image-label">Original</span>
                        </div>
                        <div class="image-container segmented">
                            <img id="segmented-image" src="" alt="Segmented">
                            <span class="image-label">Segmented</span>
                        </div>
                    </div>
                </div>

                <!-- Clusters -->
                <div class="clusters-section">
                    <div class="cluster-group">
                        <h3>Raw Clusters (Pre-merge)</h3>
                        <div class="color-bar" id="raw-color-bar"></div>
                        <div class="cluster-details" id="raw-cluster-details"></div>
                    </div>
                    <div class="merge-panel" id="merge-panel">
                        <div class="merge-panel-header">
                            <h3>Merge Groups <span style="font-size:10px;color:#666;text-transform:none;letter-spacing:0;">(click swatch to cycle group)</span></h3>
                            <span class="merge-status" id="merge-status">Matches Algorithm</span>
                        </div>
                        <div class="merge-groups-container" id="merge-groups-container"></div>
                        <div class="merge-action-buttons">
                            <button class="btn-secondary" onclick="resetMergeGroups()">Reset to Algorithm</button>
                            <button class="btn-secondary" onclick="exportMergeCSV()">Export Merge CSV</button>
                            <button class="btn-secondary" onclick="exportMergeJSON()">Export Merge JSON</button>
                        </div>
                    </div>
                    <div class="cluster-group">
                        <h3>Merged Clusters (Significant)</h3>
                        <div class="color-bar" id="merged-color-bar"></div>
                        <div class="cluster-details" id="merged-cluster-details"></div>
                    </div>
                </div>

                <!-- Validation Form -->
                <div class="validation-section">
                    <h3>Your Assessment</h3>

                    <div class="radio-group" id="classification-options">
                        <!-- Populated by JavaScript -->
                    </div>

                    <div class="form-group">
                        <label>Confidence</label>
                        <div class="confidence-group">
                            <button class="confidence-btn" data-value="high" onclick="setConfidence('high')">High</button>
                            <button class="confidence-btn" data-value="medium" onclick="setConfidence('medium')">Medium</button>
                            <button class="confidence-btn" data-value="low" onclick="setConfidence('low')">Low</button>
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Notes (optional)</label>
                        <textarea id="notes-input" placeholder="Any observations about this classification..."></textarea>
                    </div>

                    <div class="action-buttons">
                        <button class="btn-secondary" onclick="clearValidation()">Clear</button>
                        <button class="btn-success" onclick="saveAndNext()">Save & Next</button>
                    </div>

                    <div class="shortcuts-hint">
                        Keyboard: <kbd>1-7</kbd> select option, <kbd>Enter</kbd> save & next, <kbd>&larr;</kbd><kbd>&rarr;</kbd> navigate
                    </div>
                </div>
            </div>
        </div>
    </main>

    <footer>
        <div class="export-buttons">
            <button class="btn-primary" id="export-csv-btn" onclick="exportCSV()">Export CSV</button>
            <button class="btn-secondary" id="export-json-btn" onclick="exportJSON()">Export JSON</button>
            <button class="btn-warning" onclick="exportMergeCSV()">Export Merge CSV</button>
            <button class="btn-secondary" onclick="exportMergeJSON()">Export Merge JSON</button>
        </div>
        <span class="last-saved" id="last-saved">Not saved yet</span>
    </footer>

    <script>
        // ====================================================================
        // DATA
        // ====================================================================

        const SAMPLE_DATA = {sample_data_json};

        const ARCHETYPES = ['MONO', 'DOM_ACC', 'DUAL_BAL', 'MULTI_DOM', 'MULTI_BAL'];
        const ARCHETYPE_NAMES = {{
            'MONO': 'Monochrome',
            'DOM_ACC': 'Dominant + Accent',
            'DUAL_BAL': 'Dual Balanced',
            'MULTI_DOM': 'Multi Dominant',
            'MULTI_BAL': 'Multi Balanced'
        }};

        const HSB_CLASS_LABELS = {{
            'S': 'Saturated',
            'M': 'Muted',
            'N': 'Neutral'
        }};

        const STORAGE_KEY = '{storage_key}';
        const MERGE_STORAGE_KEY = '{merge_storage_key}';
        const LEGACY_STORAGE_KEY = 'archetype_validation_results_hsb_production';
        const STRATA_MODE = '{strata_mode}';
        const MERGE_ONLY = {merge_only};
        const SAMPLE_FINGERPRINT = '{fingerprint_value}';

        // Merge group badge colors
        const MERGE_GROUP_COLORS = ['#3B82F6', '#22C55E', '#F97316', '#8B5CF6', '#EC4899'];
        const MERGE_GROUP_LETTERS = ['A', 'B', 'C', 'D', 'E'];

        // Merge algorithm constants (injected from Python; must match shared.palette_merge)
        const MERGE_LOW_CHROMA = {merge_low_chroma};
        const MERGE_NEUTRAL_CHROMA = {merge_neutral_chroma};
        const MERGE_MAX_DELTA_L = {merge_max_delta_l};
        const MERGE_HUE_THRESHOLD = {merge_hue_threshold};

        let currentIndex = 0;
        let validationResults = {{}};
        let currentConfidence = 'medium';
        let mergeLabels = {{}};
        let currentMergeGroups = [];

        // Each brand is a separate DB, so instance_id is not globally unique.
        function productKey(p) {{ return p.brand + ':' + p.instance_id; }}

        // ====================================================================
        // INITIALIZATION
        // ====================================================================

        function init() {{
            // Load saved results
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) {{
                try {{
                    validationResults = JSON.parse(saved);
                }} catch (e) {{
                    console.error('Failed to parse saved results:', e);
                    validationResults = {{}};
                }}
            }}

            // One-time migration: legacy sample_id-keyed results -> brand:instance_id keys
            if (Object.keys(validationResults).length === 0) {{
                const legacyRaw = localStorage.getItem(LEGACY_STORAGE_KEY);
                if (legacyRaw) {{
                    try {{
                        const legacy = JSON.parse(legacyRaw);
                        const migrated = {{}};
                        let count = 0;
                        for (const v of Object.values(legacy)) {{
                            if (v && v.brand !== undefined && v.instance_id !== undefined) {{
                                migrated[v.brand + ':' + v.instance_id] = v;
                                count++;
                            }}
                        }}
                        if (count > 0) {{
                            validationResults = migrated;
                            localStorage.setItem(STORAGE_KEY, JSON.stringify(validationResults));
                            console.log('Migrated ' + count + ' legacy archetype result(s) from ' + LEGACY_STORAGE_KEY + ' to brand:instance_id keys under ' + STORAGE_KEY + '.');
                        }}
                    }} catch (e) {{
                        console.error('Legacy migration failed:', e);
                    }}
                }}
            }}

            // Lightweight fingerprint drift check (does not block the UI)
            const fpSideKey = STORAGE_KEY + '__fingerprint';
            const priorFp = localStorage.getItem(fpSideKey);
            if (priorFp && priorFp !== SAMPLE_FINGERPRINT) {{
                console.warn('Sample fingerprint changed since last session (' + priorFp + ' -> ' + SAMPLE_FINGERPRINT + '). Stored labels may not align with the current sample.');
            }}
            localStorage.setItem(fpSideKey, SAMPLE_FINGERPRINT);

            // Load saved merge labels
            initMergeLabels();

            // In merge-only mode, hide the archetype validation UI + exports
            if (MERGE_ONLY) {{
                const vs = document.querySelector('.validation-section');
                if (vs) vs.style.display = 'none';
                const ecsv = document.getElementById('export-csv-btn');
                const ejson = document.getElementById('export-json-btn');
                if (ecsv) ecsv.style.display = 'none';
                if (ejson) ejson.style.display = 'none';
            }}

            // Populate product selector
            const select = document.getElementById('product-select');
            SAMPLE_DATA.products.forEach((p, i) => {{
                const option = document.createElement('option');
                option.value = i;
                const validated = validationResults[productKey(p)] ? ' [done]' : '';
                const satLabel = p.has_saturated ? 'SAT' : 'NON';
                option.textContent = `#${{i+1}}: ${{p.brand}} - ${{p.archetype}} [${{satLabel}}]${{validated}}`;
                select.appendChild(option);
            }});

            // Set total count
            document.getElementById('total-count').textContent = SAMPLE_DATA.products.length;

            // Render first product
            renderProduct(0);
            updateProgress();

            // Keyboard shortcuts
            document.addEventListener('keydown', handleKeydown);
        }}

        // ====================================================================
        // NAVIGATION
        // ====================================================================

        function renderProduct(index) {{
            if (index < 0 || index >= SAMPLE_DATA.products.length) return;

            currentIndex = index;
            const product = SAMPLE_DATA.products[index];

            // Update header info
            document.getElementById('brand-tag').textContent = product.brand_label;
            document.getElementById('brand-tag').className = `brand-tag brand-${{product.brand}}`;
            document.getElementById('instance-id').textContent = `id:${{product.instance_id}}`;
            document.getElementById('product-title').textContent = product.title || 'Unknown Product';
            document.getElementById('archetype-badge').textContent = product.archetype;
            document.getElementById('current-index').textContent = index + 1;

            // Update meta badges (HSB saturation + coverage)
            const metaBadges = document.getElementById('meta-badges');
            metaBadges.innerHTML = '';

            // has_saturated badge
            const satBadge = document.createElement('span');
            satBadge.className = `meta-badge ${{product.has_saturated ? 'badge-saturated' : 'badge-not-saturated'}}`;
            satBadge.textContent = product.has_saturated ? 'HAS SATURATED' : 'NO SATURATED';
            metaBadges.appendChild(satBadge);

            // coverage_1 badge
            const covBadge = document.createElement('span');
            covBadge.className = 'meta-badge badge-coverage';
            const cov1 = product.coverage_1 !== undefined ? product.coverage_1.toFixed(1) : '?';
            covBadge.textContent = `Cov1: ${{cov1}}%`;
            metaBadges.appendChild(covBadge);

            // Update images
            const origImg = document.getElementById('original-image');
            const segImg = document.getElementById('segmented-image');

            if (product.image_path) {{
                origImg.src = 'file:///' + product.image_path.replace(/\\\\/g, '/');
                origImg.style.display = 'block';
            }} else {{
                origImg.style.display = 'none';
            }}

            if (product.segmented_path) {{
                segImg.src = 'file:///' + product.segmented_path.replace(/\\\\/g, '/');
                segImg.style.display = 'block';
            }} else {{
                segImg.style.display = 'none';
            }}

            // Render clusters with HSB metadata
            renderClusters('raw', product.raw_clusters || []);
            renderClusters('merged', product.merged_clusters || []);

            // Render merge labeling panel
            renderMergePanel(product);

            // Render classification options
            renderClassificationOptions(product.archetype);

            // Load existing validation if any
            loadExistingValidation(product);

            // Update selector
            document.getElementById('product-select').value = index;
        }}

        function renderClusters(type, clusters) {{
            const barEl = document.getElementById(`${{type}}-color-bar`);
            const detailsEl = document.getElementById(`${{type}}-cluster-details`);

            barEl.innerHTML = '';
            detailsEl.innerHTML = '';

            if (clusters.length === 0) {{
                barEl.innerHTML = '<div style="color:#666;padding:5px;">No clusters</div>';
                return;
            }}

            clusters.forEach(c => {{
                // Color bar swatch
                const swatch = document.createElement('div');
                swatch.className = 'color-swatch';
                swatch.style.background = c.hex;
                swatch.style.flexBasis = `${{c.perc}}%`;
                swatch.textContent = `${{Math.round(c.perc)}}%`;
                barEl.appendChild(swatch);

                // Detail row
                const row = document.createElement('div');
                row.className = 'cluster-row';

                const miniSwatch = document.createElement('div');
                miniSwatch.className = 'cluster-mini-swatch';
                miniSwatch.style.background = c.hex;

                const hueStr = c.hue !== null && c.hue !== undefined ? `H:${{Math.round(c.hue)}}` : 'H:--';
                const chromaStr = c.chroma !== undefined ? `C:${{Math.round(c.chroma)}}` : '';
                const satType = c.sat_type || '';
                const merged = c.n_merged > 1 ? ` (${{c.n_merged}})` : '';

                // HSB saturation info
                const hsbSat = c.hsb_sat !== undefined ? c.hsb_sat.toFixed(1) : '?';
                const hsbClass = c.hsb_class || '?';
                const hsbBadgeClass = `hsb-${{hsbClass}}`;

                const label = document.createElement('span');
                label.className = 'cluster-label';
                label.innerHTML = `${{Math.round(c.perc)}}% L:${{Math.round(c.lab_l)}} a:${{Math.round(c.lab_a)}} b:${{Math.round(c.lab_b)}} ${{chromaStr}} ${{hueStr}} [${{satType}}]${{merged}} <span class="hsb-badge ${{hsbBadgeClass}}">${{hsbClass}} ${{hsbSat}}%</span>`;

                row.appendChild(miniSwatch);
                row.appendChild(label);
                detailsEl.appendChild(row);
            }});
        }}

        function renderClassificationOptions(currentArchetype) {{
            const container = document.getElementById('classification-options');
            container.innerHTML = '';

            // CORRECT option
            const correctOption = createRadioOption('CORRECT', `CORRECT (keep as ${{currentArchetype}})`, 'correct');
            container.appendChild(correctOption);

            // Archetype options
            ARCHETYPES.forEach(arch => {{
                const option = createRadioOption(arch, `${{arch}} - ${{ARCHETYPE_NAMES[arch]}}`);
                container.appendChild(option);
            }});

            // UNCERTAIN option
            const uncertainOption = createRadioOption('UNCERTAIN', 'UNCERTAIN (skip)', 'uncertain');
            container.appendChild(uncertainOption);
        }}

        function createRadioOption(value, label, extraClass = '') {{
            const div = document.createElement('div');
            div.className = `radio-option ${{extraClass}}`;
            div.dataset.value = value;
            div.onclick = () => selectClassification(value);

            div.innerHTML = `
                <div class="radio-circle"></div>
                <span class="radio-label">${{label}}</span>
            `;

            return div;
        }}

        function selectClassification(value) {{
            // Update UI
            document.querySelectorAll('.radio-option').forEach(opt => {{
                opt.classList.remove('selected');
            }});
            const selected = document.querySelector(`.radio-option[data-value="${{value}}"]`);
            if (selected) {{
                selected.classList.add('selected');
            }}
        }}

        function setConfidence(value) {{
            currentConfidence = value;
            document.querySelectorAll('.confidence-btn').forEach(btn => {{
                btn.classList.toggle('selected', btn.dataset.value === value);
            }});
        }}

        function loadExistingValidation(product) {{
            const existing = validationResults[productKey(product)];

            // Reset form
            document.querySelectorAll('.radio-option').forEach(opt => opt.classList.remove('selected'));
            document.querySelectorAll('.confidence-btn').forEach(btn => btn.classList.remove('selected'));
            document.getElementById('notes-input').value = '';
            currentConfidence = 'medium';

            if (existing) {{
                selectClassification(existing.human_classification);
                setConfidence(existing.confidence || 'medium');
                document.getElementById('notes-input').value = existing.notes || '';
            }} else {{
                // Default confidence to medium
                setConfidence('medium');
            }}
        }}

        function nextProduct() {{
            if (currentIndex < SAMPLE_DATA.products.length - 1) {{
                renderProduct(currentIndex + 1);
            }}
        }}

        function prevProduct() {{
            if (currentIndex > 0) {{
                renderProduct(currentIndex - 1);
            }}
        }}

        function jumpToIndex(index) {{
            renderProduct(parseInt(index));
        }}

        function jumpToUnvalidated() {{
            for (let i = 0; i < SAMPLE_DATA.products.length; i++) {{
                const product = SAMPLE_DATA.products[i];
                if (!validationResults[productKey(product)]) {{
                    renderProduct(i);
                    return;
                }}
            }}
            alert('All products have been validated!');
        }}

        // ====================================================================
        // VALIDATION
        // ====================================================================

        function saveResult() {{
            const product = SAMPLE_DATA.products[currentIndex];
            const selectedOption = document.querySelector('.radio-option.selected');

            if (!selectedOption) {{
                return false;
            }}

            const humanClassification = selectedOption.dataset.value;
            const notes = document.getElementById('notes-input').value.trim();

            validationResults[productKey(product)] = {{
                sample_id: product.sample_id,
                instance_id: product.instance_id,
                brand: product.brand,
                original_classification: product.archetype,
                human_classification: humanClassification,
                is_correct: humanClassification === 'CORRECT' || humanClassification === product.archetype,
                confidence: currentConfidence,
                has_saturated: product.has_saturated,
                coverage_1: product.coverage_1,
                notes: notes,
                timestamp: new Date().toISOString()
            }};

            // Save to localStorage
            localStorage.setItem(STORAGE_KEY, JSON.stringify(validationResults));

            // Update UI
            updateProgress();
            updateLastSaved();
            updateProductSelector();

            return true;
        }}

        function saveAndNext() {{
            if (saveResult()) {{
                nextProduct();
            }} else {{
                alert('Please select a classification option.');
            }}
        }}

        function clearValidation() {{
            const product = SAMPLE_DATA.products[currentIndex];
            delete validationResults[productKey(product)];
            localStorage.setItem(STORAGE_KEY, JSON.stringify(validationResults));

            // Reset form
            loadExistingValidation(product);
            updateProgress();
            updateProductSelector();
        }}

        function updateProgress() {{
            const validated = Object.keys(validationResults).length;
            const total = SAMPLE_DATA.products.length;
            const pct = total > 0 ? (validated / total * 100) : 0;

            document.getElementById('validated-count').textContent = validated;
            document.getElementById('progress-fill').style.width = `${{pct}}%`;
        }}

        function updateLastSaved() {{
            const now = new Date();
            document.getElementById('last-saved').textContent =
                `Last saved: ${{now.toLocaleTimeString()}}`;
        }}

        function updateProductSelector() {{
            const select = document.getElementById('product-select');
            SAMPLE_DATA.products.forEach((p, i) => {{
                const validated = validationResults[productKey(p)] ? ' [done]' : '';
                const satLabel = p.has_saturated ? 'SAT' : 'NON';
                select.options[i].textContent = `#${{i+1}}: ${{p.brand}} - ${{p.archetype}} [${{satLabel}}]${{validated}}`;
            }});
        }}

        // ====================================================================
        // EXPORT
        // ====================================================================

        function exportCSV() {{
            if (MERGE_ONLY) return;
            const headers = [
                'sample_id', 'instance_id', 'brand', 'original_classification',
                'human_classification', 'is_correct', 'confidence',
                'has_saturated', 'coverage_1', 'notes', 'timestamp', 'strata_mode'
            ];

            let csv = headers.join(',') + '\\n';

            for (const result of Object.values(validationResults)) {{
                const row = [
                    result.sample_id,
                    result.instance_id,
                    result.brand,
                    result.original_classification,
                    result.human_classification,
                    result.is_correct,
                    result.confidence,
                    result.has_saturated,
                    result.coverage_1,
                    `"${{(result.notes || '').replace(/"/g, '""')}}"`,
                    result.timestamp,
                    STRATA_MODE
                ];
                csv += row.join(',') + '\\n';
            }}

            downloadFile(csv, '{results_export_name}', 'text/csv');
        }}

        function exportJSON() {{
            if (MERGE_ONLY) return;
            const data = {{
                exported_at: new Date().toISOString(),
                strata_mode: STRATA_MODE,
                total_validated: Object.keys(validationResults).length,
                total_products: SAMPLE_DATA.products.length,
                results: Object.values(validationResults)
            }};

            downloadFile(JSON.stringify(data, null, 2), '{results_export_name}'.replace(/\\.csv$/, '.json'), 'application/json');
        }}

        function downloadFile(content, filename, mimeType) {{
            const blob = new Blob([content], {{ type: mimeType }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }}

        // ====================================================================
        // KEYBOARD SHORTCUTS
        // ====================================================================

        function handleKeydown(e) {{
            // Ignore if typing in textarea
            if (e.target.tagName === 'TEXTAREA') return;

            switch (e.key) {{
                case '1':
                    selectClassification('CORRECT');
                    break;
                case '2':
                    selectClassification('MONO');
                    break;
                case '3':
                    selectClassification('DOM_ACC');
                    break;
                case '4':
                    selectClassification('DUAL_BAL');
                    break;
                case '5':
                    selectClassification('MULTI_DOM');
                    break;
                case '6':
                    selectClassification('MULTI_BAL');
                    break;
                case '7':
                    selectClassification('UNCERTAIN');
                    break;
                case 'Enter':
                    saveAndNext();
                    break;
                case 'ArrowLeft':
                    prevProduct();
                    break;
                case 'ArrowRight':
                    nextProduct();
                    break;
            }}
        }}

        // ====================================================================
        // MERGE LABELING
        // ====================================================================

        function initMergeLabels() {{
            const saved = localStorage.getItem(MERGE_STORAGE_KEY);
            if (saved) {{
                try {{
                    mergeLabels = JSON.parse(saved);
                }} catch (e) {{
                    console.error('Failed to parse saved merge labels:', e);
                    mergeLabels = {{}};
                }}
            }}
            updateMergeProgress();
        }}

        // JS fallback: recompute algo merge groups for JSONs without algo_merge_groups
        function computeAlgoGroupsFallback(rawClusters) {{
            const n = rawClusters.length;
            if (n === 0) return [];
            if (n === 1) return [0];

            function getChroma(a, b) {{ return Math.sqrt(a * a + b * b); }}
            function getHueAngle(a, b) {{
                const chroma = getChroma(a, b);
                if (chroma < MERGE_NEUTRAL_CHROMA) return null;
                let angle = Math.atan2(b, a) * 180 / Math.PI;
                return ((angle % 360) + 360) % 360;
            }}
            function hueDiff(h1, h2) {{
                if (h1 === null || h2 === null) return null;
                const d = Math.abs(h1 - h2);
                return Math.min(d, 360 - d);
            }}
            function shouldMerge(c1, c2) {{
                const ch1 = getChroma(c1.lab_a, c1.lab_b);
                const ch2 = getChroma(c2.lab_a, c2.lab_b);
                const dL = Math.abs(c1.lab_l - c2.lab_l);
                if (ch1 < MERGE_LOW_CHROMA && ch2 < MERGE_LOW_CHROMA) return dL <= MERGE_MAX_DELTA_L;
                const h1 = getHueAngle(c1.lab_a, c1.lab_b);
                const h2 = getHueAngle(c2.lab_a, c2.lab_b);
                if (h1 === null && h2 === null) return dL <= MERGE_MAX_DELTA_L;
                if ((h1 === null) !== (h2 === null)) return false;
                return hueDiff(h1, h2) < MERGE_HUE_THRESHOLD;
            }}

            // Union-find
            const parent = Array.from({{length: n}}, (_, i) => i);
            function find(x) {{
                while (parent[x] !== x) {{ parent[x] = parent[parent[x]]; x = parent[x]; }}
                return x;
            }}
            function union(x, y) {{
                const px = find(x), py = find(y);
                if (px !== py) parent[px] = py;
            }}

            for (let i = 0; i < n; i++)
                for (let j = i + 1; j < n; j++)
                    if (shouldMerge(rawClusters[i], rawClusters[j]))
                        union(i, j);

            const rootMap = {{}};
            let nextGroup = 0;
            const groups = [];
            for (let i = 0; i < n; i++) {{
                const root = find(i);
                if (!(root in rootMap)) rootMap[root] = nextGroup++;
                groups.push(rootMap[root]);
            }}
            return groups;
        }}

        function getAlgoGroups(product) {{
            if (product.algo_merge_groups && product.algo_merge_groups.length > 0) {{
                return product.algo_merge_groups.slice();
            }}
            return computeAlgoGroupsFallback(product.raw_clusters || []);
        }}

        function getMergeGroupsForProduct(product) {{
            const key = productKey(product);
            if (mergeLabels[key]) {{
                return mergeLabels[key].groups.slice();
            }}
            return getAlgoGroups(product);
        }}

        function renderMergePanel(product) {{
            const container = document.getElementById('merge-groups-container');
            container.innerHTML = '';

            const rawClusters = product.raw_clusters || [];
            if (rawClusters.length === 0) {{
                container.innerHTML = '<div style="color:#666;font-size:12px;">No clusters</div>';
                return;
            }}

            currentMergeGroups = getMergeGroupsForProduct(product);

            // Group clusters by their group ID
            const groupMap = {{}};
            currentMergeGroups.forEach((g, i) => {{
                if (!groupMap[g]) groupMap[g] = [];
                groupMap[g].push(i);
            }});

            // Render each group as a row
            const sortedGroups = Object.keys(groupMap).map(Number).sort((a, b) => a - b);
            sortedGroups.forEach(groupId => {{
                const row = document.createElement('div');
                row.className = 'merge-group-row';

                // Badge
                const badge = document.createElement('div');
                badge.className = 'merge-group-badge';
                badge.style.background = MERGE_GROUP_COLORS[groupId % MERGE_GROUP_COLORS.length];
                badge.textContent = MERGE_GROUP_LETTERS[groupId % MERGE_GROUP_LETTERS.length];
                row.appendChild(badge);

                // Swatches container
                const swatches = document.createElement('div');
                swatches.className = 'merge-group-swatches';

                groupMap[groupId].forEach(clusterIdx => {{
                    const c = rawClusters[clusterIdx];
                    const swatch = document.createElement('div');
                    swatch.className = 'merge-cluster-swatch';
                    swatch.style.background = c.hex;

                    // Adapt text color to background brightness
                    const textColor = getContrastTextColor(c.hex);
                    swatch.style.color = textColor;

                    swatch.innerHTML = `${{c.hex}}<br>${{Math.round(c.perc)}}%`;
                    swatch.onclick = () => cycleClusterGroup(clusterIdx, product);
                    swatch.title = `Click to change group (L:${{Math.round(c.lab_l)}} a:${{Math.round(c.lab_a)}} b:${{Math.round(c.lab_b)}})`;
                    swatches.appendChild(swatch);
                }});

                row.appendChild(swatches);
                container.appendChild(row);
            }});

            updateMergeStatus(product);
        }}

        function getContrastTextColor(hex) {{
            // Parse hex to RGB and compute relative luminance
            const r = parseInt(hex.slice(1, 3), 16) / 255;
            const g = parseInt(hex.slice(3, 5), 16) / 255;
            const b = parseInt(hex.slice(5, 7), 16) / 255;
            const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
            return luminance > 0.5 ? '#000000' : '#ffffff';
        }}

        function cycleClusterGroup(clusterIdx, product) {{
            const currentGroup = currentMergeGroups[clusterIdx];
            const numClusters = (product.raw_clusters || []).length;

            // Build list of distinct groups to cycle through, plus "new group"
            const distinctGroups = [...new Set(currentMergeGroups)].sort((a, b) => a - b);
            const maxGroupId = Math.max(...distinctGroups);
            const canAddNew = distinctGroups.length < Math.min(numClusters, MERGE_GROUP_COLORS.length);

            // Cycle options: existing groups + optionally a new group
            const options = [...distinctGroups];
            if (canAddNew) {{
                options.push(maxGroupId + 1);  // "new group" sentinel
            }}

            // Find current position in cycle and advance
            const currentPos = options.indexOf(currentGroup);
            const nextPos = (currentPos + 1) % options.length;
            currentMergeGroups[clusterIdx] = options[nextPos];

            normalizeGroupIds();
            saveMergeLabels(product);
            renderMergePanel(product);
        }}

        function normalizeGroupIds() {{
            // Renumber groups to sequential 0, 1, 2, ...
            const seen = {{}};
            let nextId = 0;
            for (let i = 0; i < currentMergeGroups.length; i++) {{
                const g = currentMergeGroups[i];
                if (!(g in seen)) {{
                    seen[g] = nextId++;
                }}
                currentMergeGroups[i] = seen[g];
            }}
        }}

        function resetMergeGroups() {{
            const product = SAMPLE_DATA.products[currentIndex];
            const key = productKey(product);
            delete mergeLabels[key];
            localStorage.setItem(MERGE_STORAGE_KEY, JSON.stringify(mergeLabels));
            currentMergeGroups = getAlgoGroups(product);
            renderMergePanel(product);
            updateMergeProgress();
        }}

        function saveMergeLabels(product) {{
            const key = productKey(product);
            const algoGroups = getAlgoGroups(product);
            const isModified = !arraysEqual(currentMergeGroups, algoGroups);

            if (isModified) {{
                mergeLabels[key] = {{
                    instance_id: product.instance_id,
                    groups: currentMergeGroups.slice(),
                    algo_groups: algoGroups,
                    modified: true,
                    timestamp: new Date().toISOString()
                }};
            }} else {{
                // Matches algorithm — remove from storage to save space
                delete mergeLabels[key];
            }}

            localStorage.setItem(MERGE_STORAGE_KEY, JSON.stringify(mergeLabels));
            updateMergeProgress();
        }}

        function updateMergeStatus(product) {{
            const statusEl = document.getElementById('merge-status');
            const algoGroups = getAlgoGroups(product);
            const isModified = !arraysEqual(currentMergeGroups, algoGroups);

            if (isModified) {{
                statusEl.textContent = 'Modified';
                statusEl.className = 'merge-status merge-status-modified';
            }} else {{
                statusEl.textContent = 'Matches Algorithm';
                statusEl.className = 'merge-status merge-status-matches';
            }}
        }}

        function updateMergeProgress() {{
            const total = SAMPLE_DATA.products.length;
            const labeled = Object.keys(mergeLabels).length;
            const pct = total > 0 ? (labeled / total * 100) : 0;

            document.getElementById('merge-labeled-count').textContent = labeled;
            document.getElementById('merge-total-count').textContent = total;
            document.getElementById('merge-progress-fill').style.width = pct + '%';
        }}

        function arraysEqual(a, b) {{
            if (a.length !== b.length) return false;
            for (let i = 0; i < a.length; i++) {{
                if (a[i] !== b[i]) return false;
            }}
            return true;
        }}

        // ====================================================================
        // MERGE EXPORT
        // ====================================================================

        function exportMergeCSV() {{
            const headers = [
                'instance_id', 'cluster_idx', 'lab_l', 'lab_a', 'lab_b',
                'perc', 'hex', 'group', 'algo_group', 'user_modified',
                'brand', 'strata_mode'
            ];
            let csv = headers.join(',') + '\\n';

            SAMPLE_DATA.products.forEach(product => {{
                const rawClusters = product.raw_clusters || [];
                if (rawClusters.length === 0) return;

                const algoGroups = getAlgoGroups(product);
                const key = productKey(product);
                const userLabel = mergeLabels[key];
                const userGroups = userLabel ? userLabel.groups : algoGroups;
                const isModified = userLabel ? userLabel.modified : false;

                rawClusters.forEach((c, idx) => {{
                    const row = [
                        product.instance_id,
                        idx,
                        Math.round(c.lab_l),
                        Math.round(c.lab_a),
                        Math.round(c.lab_b),
                        c.perc.toFixed(2),
                        c.hex,
                        userGroups[idx] !== undefined ? userGroups[idx] : '',
                        algoGroups[idx] !== undefined ? algoGroups[idx] : '',
                        isModified,
                        product.brand,
                        STRATA_MODE
                    ];
                    csv += row.join(',') + '\\n';
                }});
            }});

            downloadFile(csv, '{merge_export_name}', 'text/csv');
        }}

        function exportMergeJSON() {{
            const products = [];

            SAMPLE_DATA.products.forEach(product => {{
                const rawClusters = product.raw_clusters || [];
                if (rawClusters.length === 0) return;

                const algoGroups = getAlgoGroups(product);
                const key = productKey(product);
                const userLabel = mergeLabels[key];
                const userGroups = userLabel ? userLabel.groups : algoGroups;

                products.push({{
                    instance_id: product.instance_id,
                    brand: product.brand,
                    user_modified: userLabel ? userLabel.modified : false,
                    clusters: rawClusters.map((c, idx) => ({{
                        cluster_idx: idx,
                        lab_l: Math.round(c.lab_l),
                        lab_a: Math.round(c.lab_a),
                        lab_b: Math.round(c.lab_b),
                        perc: c.perc,
                        hex: c.hex,
                        user_group: userGroups[idx],
                        algo_group: algoGroups[idx]
                    }}))
                }});
            }});

            const data = {{
                exported_at: new Date().toISOString(),
                strata_mode: STRATA_MODE,
                total_products: products.length,
                total_modified: products.filter(p => p.user_modified).length,
                products: products
            }};

            downloadFile(JSON.stringify(data, null, 2), '{merge_export_name}'.replace(/\\.csv$/, '.json'), 'application/json');
        }}

        // ====================================================================
        // START
        // ====================================================================

        init();
    </script>
</body>
</html>
'''


# ============================================================================
# BUILDER
# ============================================================================

def build_validator_html(input_json=DEFAULT_INPUT_JSON, output_dir=DEFAULT_OUTPUT_DIR):
    """Build the self-contained validation HTML from a sample JSON.

    Reads the sample JSON, derives the storage keys / export filenames / merge
    thresholds / merge-only mode from its metadata, renders HTML_TEMPLATE, and
    writes the result. Returns the output file path.

    The brace-integrity assertion runs against the template with all
    placeholders resolved but BEFORE the sample JSON is injected: embedded JSON
    legitimately contains '}}' sequences (adjacent closing braces of nested
    objects), so asserting on the JSON payload would false-positive. The check
    that matters is that no un-ported '{{'/'}}' literal survives in the shell.
    """
    if not os.path.exists(input_json):
        raise FileNotFoundError(
            f"Input JSON not found: {input_json}. "
            f"Run 'archetype-validation-sample' (or 'archetype-review') first."
        )

    with open(input_json, 'r', encoding='utf-8') as f:
        sample_data = json.load(f)

    metadata = sample_data.get('metadata', {}) or {}
    strata_mode = metadata.get('strata_mode', 'archetype')
    fingerprint = metadata.get('sample_fingerprint', '')
    brand = metadata.get('brand')

    merge_only = 'true' if strata_mode == 'pair-features' else 'false'

    # localStorage keys (versioned, namespaced by strata + fingerprint)
    storage_key = f"archetype_validation_results_v2_{strata_mode}_{fingerprint}"
    merge_storage_key = f"archetype_merge_labels_v1_{strata_mode}_{fingerprint}"

    # Export filenames
    if strata_mode == 'pair-features':
        output_filename = 'archetype_validation_pairfeatures.html'
        merge_export_name = 'merge_labels_pairfeatures.csv'
    else:
        output_filename = 'archetype_validation.html'
        if brand:
            merge_export_name = f"merge_labels_{brand}.csv"
        else:
            merge_export_name = 'merge_labels_all_brands.csv'

    results_export_name = f"validation_results_{strata_mode}.csv"

    # Placeholders shared across the shell + injected merge thresholds.
    # sample_data_json is injected LAST (via sentinel) so the brace-integrity
    # assertion does not trip over the JSON's own '}}' sequences.
    _SENTINEL = '__SAMPLE_DATA_JSON_SENTINEL__'
    shell = HTML_TEMPLATE.format(
        sample_data_json=_SENTINEL,
        storage_key=storage_key,
        merge_storage_key=merge_storage_key,
        merge_export_name=merge_export_name,
        results_export_name=results_export_name,
        strata_mode=strata_mode,
        merge_only=merge_only,
        fingerprint_value=fingerprint,
        merge_low_chroma=LOW_CHROMA_MERGE_THRESHOLD,
        merge_neutral_chroma=NEUTRAL_CHROMA_THRESHOLD,
        merge_max_delta_l=LOW_CHROMA_MAX_DELTA_L,
        merge_hue_threshold=HUE_MERGE_THRESHOLD,
    )

    assert '{{' not in shell and '}}' not in shell, (
        "Un-ported template braces survived .format(): the shell still contains "
        "'{{' or '}}'. A literal CSS/JS brace was over-doubled or a placeholder "
        "was mistyped. Fix the HTML_TEMPLATE before writing output."
    )

    sample_data_json = json.dumps(sample_data, ensure_ascii=False)
    html_content = shell.replace(_SENTINEL, sample_data_json)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML validation tool with HSB metadata + merge labeling (READ-ONLY)"
    )
    parser.add_argument('--input-json', default=DEFAULT_INPUT_JSON,
                        help="Path to validation sample JSON")
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help="Output directory for HTML file")

    args, _ = parser.parse_known_args()

    print("=" * 70)
    print("Validation HTML Generator (HSB Production)")
    print("=" * 70)
    print("\nThis script is READ-ONLY:")
    print("  - Only reads validation_sample.json")
    print("  - Creates NEW HTML file")
    print("  - Validation + merge labels stored in browser localStorage")

    if not os.path.exists(args.input_json):
        print(f"\nERROR: Input JSON not found: {args.input_json}")
        print("Please run the sample step first (python cli.py archetype-validation-sample),")
        print("or use the one-shot 'python cli.py archetype-review'.")
        sys.exit(1)

    print(f"\n[1/2] Loading sample data...")
    with open(args.input_json, 'r', encoding='utf-8') as f:
        _preview = json.load(f)
    print(f"  Loaded {len(_preview['products'])} products")

    print(f"\n[2/2] Generating HTML...")
    output_path = build_validator_html(input_json=args.input_json, output_dir=args.output_dir)
    print(f"  Saved to: {output_path}")

    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  Products in sample: {len(_preview['products'])}")
    print(f"  Output: {output_path}")
    print(f"\nFeatures:")
    print(f"  - HSB saturation badges (S/M/N) per cluster")
    print(f"  - has_saturated status prominently displayed")
    print(f"  - coverage_1 value on product card")
    print(f"  - Merge-group labeling panel (click swatch to cycle group)")
    print(f"  - Keyboard shortcuts: 1-7 select, Enter save, arrows navigate")
    print(f"  - localStorage persistence + CSV/JSON export (archetype + merge)")
    print(f"\nNext steps:")
    print(f"  1. Open the generated HTML in your browser")
    print(f"  2. Validate products / correct merge groups using the interface")
    print(f"  3. Click 'Export CSV' when done")
    print("=" * 70)


if __name__ == '__main__':
    main()
