"""
ENTRY - run: python cli.py archetype-distribution  (distribution tables -> archetype_distribution.html).

Archetype Distribution Tables (All Products)
=============================================

Generates an HTML file with summary tables showing product counts by brand
and archetype for ALL products (no saturation filter), with new analyses:
- MONO + DOM_ACC combined rate
- Per-gender archetype distribution
- Top-cluster coverage distribution

Uses Plotly for interactive heatmap visualizations.

This script is READ-ONLY:
- Only reads all_products_classified_hsb.csv
- Creates NEW HTML file in outputs folder

Usage:
    docker compose run --rm pipeline python cli.py archetype-distribution
"""

import os
import sys
import argparse
from datetime import datetime
import pandas as pd
import numpy as np

# Script directory for self-contained paths
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Configuration - reads the classifier's master CSV from this slice's outputs/
DEFAULT_INPUT_CSV = os.path.join(_SCRIPT_DIR, 'outputs', 'all_products_classified_hsb.csv')
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs')

BRAND_ORDER = ['nike', 'lulu', 'ua', 'adidas', 'puma']
BRAND_LABELS = {
    'nike': 'Nike',
    'lulu': 'Lululemon',
    'ua': 'Under Armour',
    'adidas': 'Adidas',
    'puma': 'Puma'
}

ARCHETYPE_ORDER = ['MONO', 'DOM_ACC', 'DUAL_BAL', 'MULTI_DOM', 'MULTI_BAL']

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Archetype Distribution Tables (All Products)</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            padding: 40px;
            line-height: 1.6;
        }}

        h1 {{
            color: #3B82F6;
            margin-bottom: 10px;
            font-size: 28px;
        }}

        .subtitle {{
            color: #9CA3AF;
            margin-bottom: 30px;
            font-size: 14px;
        }}

        .summary-box {{
            background: #222;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 30px;
            display: flex;
            gap: 40px;
            flex-wrap: wrap;
        }}

        .summary-stat {{
            display: flex;
            flex-direction: column;
        }}

        .summary-stat .label {{
            color: #9CA3AF;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .summary-stat .value {{
            color: #22C55E;
            font-size: 24px;
            font-weight: 600;
        }}

        .table-section {{
            background: #222;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 25px;
        }}

        .table-section h2 {{
            color: #e0e0e0;
            font-size: 16px;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid #333;
        }}

        .table-section h2 .table-num {{
            color: #3B82F6;
            margin-right: 10px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        th, td {{
            padding: 10px 12px;
            text-align: right;
            border-bottom: 1px solid #333;
        }}

        th {{
            background: #2a2a2a;
            color: #9CA3AF;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }}

        th:first-child, td:first-child {{
            text-align: left;
        }}

        tr:hover {{
            background: #2a2a2a;
        }}

        tr.total-row {{
            background: #2a2a2a;
            font-weight: 600;
        }}

        tr.total-row td {{
            border-top: 2px solid #444;
        }}

        .highlight {{
            color: #F97316;
            font-weight: 600;
        }}

        .negative {{
            color: #EF4444;
        }}

        .positive {{
            color: #22C55E;
        }}

        .brand-nike {{ color: #F97316; }}
        .brand-lulu {{ color: #EC4899; }}
        .brand-ua {{ color: #6B7280; }}
        .brand-adidas {{ color: #3B82F6; }}
        .brand-puma {{ color: #22C55E; }}

        .insight-box {{
            background: #1E3A5F;
            border-left: 4px solid #3B82F6;
            padding: 15px;
            margin-top: 15px;
            font-size: 13px;
            color: #9CA3AF;
        }}

        .insight-box strong {{
            color: #e0e0e0;
        }}

        .footer {{
            text-align: center;
            color: #666;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #333;
            font-size: 12px;
        }}

        /* Heatmap styles */
        .heatmap td:not(:first-child) {{
            text-align: center;
            font-weight: 500;
        }}

        .heatmap td.heat-cell {{
            color: white;
            text-shadow: 0 0 2px rgba(0,0,0,0.5);
        }}
    </style>
</head>
<body>
    <h1>Archetype Distribution Tables (All Products)</h1>
    <p class="subtitle">Brand x Archetype breakdown for all products — no saturation filter applied</p>

    <div class="summary-box">
        <div class="summary-stat">
            <span class="label">Total Products</span>
            <span class="value">{total_products:,}</span>
        </div>
        <div class="summary-stat">
            <span class="label">MONO + DOM_ACC</span>
            <span class="value">{mono_domacc_count:,}</span>
        </div>
        <div class="summary-stat">
            <span class="label">MONO + DOM_ACC %</span>
            <span class="value">{mono_domacc_pct:.1f}%</span>
        </div>
    </div>

    {tables_html}

    <div class="footer">
        Generated on {timestamp}<br>
        Source: all_products_classified_hsb.csv (all products, no saturation filter)
    </div>
</body>
</html>
'''


# ============================================================================
# DATA LOADING
# ============================================================================

def load_data(csv_path: str) -> pd.DataFrame:
    """Load classified products CSV."""
    print(f"Loading data from: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df):,} products")
    return df


# ============================================================================
# TABLE GENERATORS
# ============================================================================

def generate_count_table(df: pd.DataFrame) -> pd.DataFrame:
    """Generate product count cross-tabulation."""
    pivot = pd.crosstab(df['brand'], df['archetype'], margins=True)

    # Reorder columns
    cols = [c for c in ARCHETYPE_ORDER if c in pivot.columns] + ['All']
    pivot = pivot[[c for c in cols if c in pivot.columns]]

    # Reorder rows
    rows = [b for b in BRAND_ORDER if b in pivot.index] + ['All']
    pivot = pivot.reindex([r for r in rows if r in pivot.index])

    return pivot


def generate_percentage_table(df: pd.DataFrame) -> pd.DataFrame:
    """Generate percentage within brand table."""
    pct = df.groupby('brand')['archetype'].value_counts(normalize=True).unstack().fillna(0) * 100

    # Reorder columns
    pct = pct[[c for c in ARCHETYPE_ORDER if c in pct.columns]]

    # Reorder rows
    pct = pct.reindex([b for b in BRAND_ORDER if b in pct.index])

    return pct.round(1)


def generate_gender_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate per-gender archetype distribution table.

    Returns DataFrame with columns: brand, gender, and one column per archetype (%).
    """
    rows = []
    for brand in BRAND_ORDER:
        brand_df = df[df['brand'] == brand]
        if len(brand_df) == 0:
            continue
        for gender in ['mens', 'womens']:
            subset = brand_df[brand_df['gender'] == gender]
            if len(subset) == 0:
                continue
            row = {'brand': brand, 'gender': gender, 'n_products': len(subset)}
            arch_counts = subset['archetype'].value_counts()
            for arch in ARCHETYPE_ORDER:
                count = arch_counts.get(arch, 0)
                row[arch] = round(count / len(subset) * 100, 1)
            rows.append(row)

    return pd.DataFrame(rows)


def generate_mono_domacc_rate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute (MONO + DOM_ACC) / total per brand, with overall rate.
    """
    rows = []
    for brand in BRAND_ORDER:
        brand_df = df[df['brand'] == brand]
        if len(brand_df) == 0:
            continue
        mono_domacc = brand_df[brand_df['archetype'].isin(['MONO', 'DOM_ACC'])]
        mono_count = len(brand_df[brand_df['archetype'] == 'MONO'])
        domacc_count = len(brand_df[brand_df['archetype'] == 'DOM_ACC'])
        combined = len(mono_domacc)
        rows.append({
            'brand': brand,
            'n_products': len(brand_df),
            'mono_count': mono_count,
            'mono_pct': round(mono_count / len(brand_df) * 100, 1),
            'domacc_count': domacc_count,
            'domacc_pct': round(domacc_count / len(brand_df) * 100, 1),
            'combined_count': combined,
            'combined_pct': round(combined / len(brand_df) * 100, 1),
        })

    # Overall row
    total = len(df)
    mono_total = len(df[df['archetype'] == 'MONO'])
    domacc_total = len(df[df['archetype'] == 'DOM_ACC'])
    combined_total = mono_total + domacc_total
    rows.append({
        'brand': 'Overall',
        'n_products': total,
        'mono_count': mono_total,
        'mono_pct': round(mono_total / total * 100, 1),
        'domacc_count': domacc_total,
        'domacc_pct': round(domacc_total / total * 100, 1),
        'combined_count': combined_total,
        'combined_pct': round(combined_total / total * 100, 1),
    })

    return pd.DataFrame(rows)


def generate_coverage_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute % of products with coverage_1 >= 70%, 80%, 90%
    (overall and per brand), plus mean/median coverage_1.
    """
    thresholds = [70, 80, 90]
    rows = []

    def compute_row(subset, label):
        if len(subset) == 0:
            return None
        cov = subset['coverage_1']
        row = {
            'brand': label,
            'n_products': len(subset),
            'mean_coverage_1': round(cov.mean(), 1),
            'median_coverage_1': round(cov.median(), 1),
        }
        for t in thresholds:
            count = (cov >= t).sum()
            row[f'pct_gte_{t}'] = round(count / len(subset) * 100, 1)
        return row

    for brand in BRAND_ORDER:
        brand_df = df[df['brand'] == brand]
        row = compute_row(brand_df, brand)
        if row:
            rows.append(row)

    overall = compute_row(df, 'Overall')
    if overall:
        rows.append(overall)

    return pd.DataFrame(rows)


# ============================================================================
# PLOTLY HEATMAP
# ============================================================================

def create_plotly_heatmap(df: pd.DataFrame, title: str, value_format: str = 'count') -> str:
    """
    Create a Plotly heatmap and return as HTML div.

    Args:
        df: DataFrame with brands as index, archetypes as columns
        title: Chart title
        value_format: 'count' for integers, 'percent' for percentages

    Returns:
        HTML string containing the Plotly chart
    """
    # Plotly imported lazily (deferred) to keep module import cheap.
    import plotly.graph_objects as go

    # Prepare data (exclude 'All' row/column for the heatmap)
    plot_df = df.drop('All', axis=0, errors='ignore').drop('All', axis=1, errors='ignore')

    # Build plain Python lists (required for Plotly)
    x_labels = list(plot_df.columns)
    y_labels = [BRAND_LABELS.get(b, b) for b in plot_df.index]

    # Build z matrix and text matrix as plain lists
    if value_format == 'count':
        # Use log scale for display (range is 300 to 230,000)
        z_matrix = [[np.log10(v + 1) for v in row] for row in plot_df.values]
        text_matrix = [[f"{int(v):,}" for v in row] for row in plot_df.values]
    else:
        z_matrix = [list(row) for row in plot_df.values]
        text_matrix = [[f"{v:.1f}%" for v in row] for row in plot_df.values]

    fig = go.Figure(data=go.Heatmap(
        z=z_matrix,
        x=x_labels,
        y=y_labels,
        colorscale='sunsetdark',
        text=text_matrix,
        texttemplate="%{text}",
        textfont={"size": 12},
        hoverongaps=False,
        showscale=False
    ))

    fig.update_layout(
        height=300,
        width=700,
        paper_bgcolor='#222',
        plot_bgcolor='#222',
        font=dict(color='#e0e0e0'),
        margin=dict(l=120, r=40, t=20, b=60),
        xaxis=dict(tickangle=0)
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


# ============================================================================
# HTML FORMATTERS
# ============================================================================

def format_count_table_heatmap_html(df: pd.DataFrame, title: str, table_num: int) -> str:
    """Format count table as Plotly heatmap HTML."""
    heatmap_html = create_plotly_heatmap(df, '', value_format='count')

    return f'''
    <div class="table-section">
        <h2><span class="table-num">Table {table_num}</span>{title}</h2>
        {heatmap_html}
    </div>
    '''


def format_percentage_table_heatmap_html(df: pd.DataFrame, title: str, table_num: int) -> str:
    """Format percentage table as Plotly heatmap HTML."""
    heatmap_html = create_plotly_heatmap(df, '', value_format='percent')

    return f'''
    <div class="table-section">
        <h2><span class="table-num">Table {table_num}</span>{title}</h2>
        {heatmap_html}
    </div>
    '''


def format_gender_table_html(df: pd.DataFrame, title: str, table_num: int) -> str:
    """Format per-gender archetype distribution as HTML table."""
    gender_labels = {'mens': 'Mens', 'womens': 'Womens'}

    html = f'''
    <div class="table-section">
        <h2><span class="table-num">Table {table_num}</span>{title}</h2>
        <table>
            <thead>
                <tr>
                    <th>Brand</th>
                    <th>Gender</th>
                    <th>Products</th>
    '''

    for arch in ARCHETYPE_ORDER:
        html += f'<th>{arch}</th>'
    html += '</tr></thead><tbody>'

    for _, row in df.iterrows():
        brand = row['brand']
        brand_label = BRAND_LABELS.get(brand, brand)
        brand_class = f'brand-{brand}' if brand in BRAND_LABELS else ''

        html += f'<tr><td class="{brand_class}">{brand_label}</td>'
        html += f'<td>{gender_labels.get(row["gender"], row["gender"])}</td>'
        html += f'<td>{int(row["n_products"]):,}</td>'
        for arch in ARCHETYPE_ORDER:
            val = row.get(arch, 0)
            html += f'<td>{val:.1f}%</td>'
        html += '</tr>'

    html += '</tbody></table></div>'
    return html


def format_mono_domacc_rate_html(df: pd.DataFrame, title: str, table_num: int) -> str:
    """Format MONO + DOM_ACC rate table as HTML."""
    html = f'''
    <div class="table-section">
        <h2><span class="table-num">Table {table_num}</span>{title}</h2>
        <table>
            <thead>
                <tr>
                    <th>Brand</th>
                    <th>Products</th>
                    <th>MONO %</th>
                    <th>DOM_ACC %</th>
                    <th>Combined %</th>
                </tr>
            </thead>
            <tbody>
    '''

    max_combined = df['combined_pct'].max()

    for _, row in df.iterrows():
        brand = row['brand']
        is_total = brand == 'Overall'
        row_class = ' class="total-row"' if is_total else ''
        brand_label = BRAND_LABELS.get(brand, brand)
        brand_class = f'brand-{brand}' if brand in BRAND_LABELS else ''

        highlight = ' class="highlight"' if row['combined_pct'] == max_combined and not is_total else ''

        html += f'<tr{row_class}>'
        html += f'<td class="{brand_class}">{brand_label}</td>'
        html += f'<td>{int(row["n_products"]):,}</td>'
        html += f'<td>{row["mono_pct"]:.1f}%</td>'
        html += f'<td>{row["domacc_pct"]:.1f}%</td>'
        html += f'<td{highlight}>{row["combined_pct"]:.1f}%</td>'
        html += '</tr>'

    html += '</tbody></table></div>'
    return html


def format_coverage_distribution_html(df: pd.DataFrame, title: str, table_num: int) -> str:
    """Format coverage distribution table as HTML with insight box."""
    html = f'''
    <div class="table-section">
        <h2><span class="table-num">Table {table_num}</span>{title}</h2>
        <table>
            <thead>
                <tr>
                    <th>Brand</th>
                    <th>Products</th>
                    <th>Mean Cov1</th>
                    <th>Median Cov1</th>
                    <th>&ge; 70%</th>
                    <th>&ge; 80%</th>
                    <th>&ge; 90%</th>
                </tr>
            </thead>
            <tbody>
    '''

    for _, row in df.iterrows():
        brand = row['brand']
        is_total = brand == 'Overall'
        row_class = ' class="total-row"' if is_total else ''
        brand_label = BRAND_LABELS.get(brand, brand)
        brand_class = f'brand-{brand}' if brand in BRAND_LABELS else ''

        html += f'<tr{row_class}>'
        html += f'<td class="{brand_class}">{brand_label}</td>'
        html += f'<td>{int(row["n_products"]):,}</td>'
        html += f'<td>{row["mean_coverage_1"]:.1f}%</td>'
        html += f'<td>{row["median_coverage_1"]:.1f}%</td>'
        html += f'<td>{row["pct_gte_70"]:.1f}%</td>'
        html += f'<td>{row["pct_gte_80"]:.1f}%</td>'
        html += f'<td>{row["pct_gte_90"]:.1f}%</td>'
        html += '</tr>'

    html += '''</tbody></table>
        <div class="insight-box">
            <strong>Why this matters for CIEDE2000 validation:</strong>
            CIEDE2000 agglomerative clustering groups products by their dominant color only.
            This is valid if most products have a single clearly dominant color (high coverage_1).
            If coverage_1 is consistently high (e.g., &ge; 70% for most products), then the dominant
            color is a reliable proxy for the product's overall palette, and CIEDE2000 grouping
            will produce perceptually coherent clusters.
        </div>
    </div>'''
    return html


# ============================================================================
# HTML GENERATION
# ============================================================================

def generate_html(df: pd.DataFrame, output_path: str):
    """Generate complete HTML file with all tables."""

    tables_html = ''

    # Table 1: Counts (All Products) - HEATMAP
    table1 = generate_count_table(df)
    tables_html += format_count_table_heatmap_html(table1, "Product Counts by Brand and Archetype", 1)

    # Table 2: Percentages (All Products) - HEATMAP
    table2 = generate_percentage_table(df)
    tables_html += format_percentage_table_heatmap_html(table2, "Percentages Within Each Brand", 2)

    # Table 3: Per-Gender Distribution
    table3 = generate_gender_table(df)
    tables_html += format_gender_table_html(table3, "Per-Gender Archetype Distribution", 3)

    # Table 4: MONO + DOM_ACC Rate
    table4 = generate_mono_domacc_rate(df)
    tables_html += format_mono_domacc_rate_html(table4, "MONO + DOM_ACC Rate by Brand", 4)

    # Table 5: Coverage Distribution
    table5 = generate_coverage_distribution(df)
    tables_html += format_coverage_distribution_html(table5, "Top-Cluster Coverage Distribution", 5)

    # Summary stats for template
    mono_domacc = df[df['archetype'].isin(['MONO', 'DOM_ACC'])]
    mono_domacc_count = len(mono_domacc)
    mono_domacc_pct = mono_domacc_count / len(df) * 100

    # Build final HTML
    html = HTML_TEMPLATE.format(
        total_products=len(df),
        mono_domacc_count=mono_domacc_count,
        mono_domacc_pct=mono_domacc_pct,
        tables_html=tables_html,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    # Write file
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"HTML saved to: {output_path}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    # No brand/gender args — the distribution grid covers all brands/genders.
    parser = argparse.ArgumentParser(
        description="Generate archetype distribution tables as HTML (READ-ONLY, all products)"
    )
    parser.add_argument('--input-csv', default=DEFAULT_INPUT_CSV,
                        help="Path to classified products CSV")
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help="Output directory for HTML file")

    args, _ = parser.parse_known_args()

    # Check input file
    if not os.path.exists(args.input_csv):
        print(f"ERROR: Input CSV not found: {args.input_csv}")
        print(f"Run classify_archetypes.py first to generate the input file.")
        sys.exit(1)

    # Load data (no saturation filter)
    df = load_data(args.input_csv)

    # Summary stats
    mono_domacc = df[df['archetype'].isin(['MONO', 'DOM_ACC'])]
    print(f"MONO + DOM_ACC: {len(mono_domacc):,} ({len(mono_domacc)/len(df)*100:.1f}% of all)")

    # Generate HTML
    output_path = os.path.join(args.output_dir, 'archetype_distribution.html')
    generate_html(df, output_path)

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  Total products: {len(df):,}")
    print(f"  MONO + DOM_ACC: {len(mono_domacc):,} ({len(mono_domacc)/len(df)*100:.1f}%)")
    print(f"  Output: {output_path}")
    print("=" * 70)


if __name__ == '__main__':
    main()
