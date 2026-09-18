"""
ENTRY - run: python cli.py visualize-coverage  (coverage grid -> coverage_distribution.html).

Coverage Distribution Visualization (HSB Saturation)
=====================================================

Renders coverage_distribution.html: a 6-row x 5-column grid of histograms
showing, for each color class (saturated / muted / neutral) x gender x brand,
the distribution of per-product merged-cluster coverage percentages. Each panel
is annotated with its sample size and the % of colors that are "bimodal"
(coverage >= 80% or <= 20%).

This is the RENDER half of the coverage slice. It imports the compute entry
point from analyze_coverage — the histogram needs the raw coverage
distributions, which the analytical CSVs do not preserve.

Output:
    outputs/coverage_distribution.html
"""

import os
import numpy as np

from shared.hsb import SATURATED_HSB, NEUTRAL_HSB
from coverage.analyze_coverage import (
    compute_coverages,
    BRANDS, BRAND_LABELS,
    GENDERS, GENDER_LABELS,
    COLOR_CLASSES,
    OUTPUT_DIR,
)

# Render-only: per color-class fill color (compute has no use for these)
COLOR_CLASS_COLORS = {
    'saturated': '#e74c3c',  # Red
    'muted': '#f39c12',      # Orange
    'neutral': '#95a5a6'     # Gray
}


def create_combined_histogram_figure(all_coverages):
    """
    Create a figure with histograms for all brand/gender/color_class combinations.

    Layout: 6 rows x 5 columns
    - Rows 1-2: Saturated (mens, womens)
    - Rows 3-4: Muted (mens, womens)
    - Rows 5-6: Neutral (mens, womens)
    - Columns: Nike, Adidas, Puma, Lululemon, Under Armour
    """
    # Plotly imported lazily (deferred) to keep module import cheap.
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    # Build subplot titles
    subplot_titles = []
    for color_class in COLOR_CLASSES:
        for gender in GENDERS:
            for brand in BRANDS:
                subplot_titles.append(f"{BRAND_LABELS[brand]}")

    fig = make_subplots(
        rows=6, cols=5,
        subplot_titles=subplot_titles,
        vertical_spacing=0.06,
        horizontal_spacing=0.04,
        row_heights=[1, 1, 1, 1, 1, 1]
    )

    # Track row index
    row = 0
    for color_class_idx, color_class in enumerate(COLOR_CLASSES):
        for gender_idx, gender in enumerate(GENDERS):
            row += 1

            for brand_idx, brand in enumerate(BRANDS):
                col = brand_idx + 1
                key = (brand, gender, color_class)
                coverages = all_coverages.get(key, np.array([]))

                if len(coverages) > 0:
                    # Add histogram
                    fig.add_trace(
                        go.Histogram(
                            x=coverages,
                            nbinsx=40,
                            marker_color=COLOR_CLASS_COLORS[color_class],
                            opacity=0.8,
                            name=f'{brand}_{gender}_{color_class}',
                            showlegend=False
                        ),
                        row=row, col=col
                    )

                    # Calculate statistics
                    n_total = len(coverages)
                    n_high = np.sum(coverages >= 80)
                    n_low = np.sum(coverages <= 20)
                    pct_bimodal = 100 * (n_high + n_low) / n_total

                    # Add annotation with stats
                    subplot_idx = (row - 1) * 5 + col
                    axis_suffix = '' if subplot_idx == 1 else str(subplot_idx)
                    fig.add_annotation(
                        x=0.95, y=0.95,
                        xref=f'x{axis_suffix} domain',
                        yref=f'y{axis_suffix} domain',
                        text=f"n={n_total:,}<br>{pct_bimodal:.0f}%",
                        showarrow=False,
                        font=dict(size=9, color='white'),
                        bgcolor='rgba(0,0,0,0.5)',
                        borderpad=3
                    )

    # Add row labels (color class + gender) on the left side
    row = 0
    for color_class in COLOR_CLASSES:
        for gender in GENDERS:
            row += 1
            # Add annotation on the left side of row
            fig.add_annotation(
                x=-0.08,
                y=1 - (row - 0.5) / 6,
                xref='paper',
                yref='paper',
                text=f"<b>{color_class.capitalize()}</b><br>{GENDER_LABELS[gender]}",
                showarrow=False,
                font=dict(size=11, color='white'),
                textangle=0,
                xanchor='right'
            )

    # Update layout
    fig.update_layout(
        title=dict(
            text=(
                'Coverage Distribution by Color Class, Brand, and Gender (HSB Saturation)<br>'
                f'<sup>Annotation shows sample size and % bimodal (coverage >=80% or <=20%) | '
                f'HSB: sat>={SATURATED_HSB}%, muted>={NEUTRAL_HSB}%, neutral<{NEUTRAL_HSB}%</sup>'
            ),
            x=0.5,
            font=dict(size=18)
        ),
        height=1200,
        width=1400,
        paper_bgcolor='#1a1a1a',
        plot_bgcolor='#1a1a1a',
        font=dict(color='white'),
        showlegend=False,
        margin=dict(l=120)
    )

    # Update axes
    fig.update_xaxes(
        range=[0, 100],
        gridcolor='#333333',
        linecolor='#333333',
        dtick=50,
        tickfont=dict(size=8)
    )
    fig.update_yaxes(
        gridcolor='#333333',
        linecolor='#333333',
        tickfont=dict(size=8)
    )

    return fig


def main():
    """
    Compute coverage distributions across all brands and render the histogram
    grid. No brand/gender args — the figure is a 5-brand comparison grid.
    """
    print("Computing coverage distributions across all brands...")
    all_coverages, _ = compute_coverages()

    print("Rendering histogram grid...")
    fig = create_combined_histogram_figure(all_coverages)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, 'coverage_distribution.html')
    fig.write_html(output_path)
    print(f"Saved: {output_path}")


if __name__ == '__main__':
    main()
