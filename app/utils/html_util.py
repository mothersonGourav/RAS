import pandas as pd


def dataframe_to_compact_html(df: pd.DataFrame, filters_used: dict = None) -> str:
    # Generate base HTML
    table_html = df.to_html(
        index=False,
        escape=False,
        border=0,
        float_format="{:,.0f}".format,
    )

    # Clean pandas defaults
    table_html = table_html.replace('class="dataframe"', "")
    table_html = table_html.replace('style="text-align: right;"', "")

    # Apply per-column alignment
    rows = table_html.split("<tr>")
    new_rows = [rows[0]] 

    for row in rows[1:]:
        if "<td>" not in row:
            new_rows.append(row)
            continue

        cells = row.split("<td>")
        new_row = cells[0]

        for i, cell in enumerate(cells[1:]):
            col = df.columns[i]
            align = (
                "right"
                if pd.api.types.is_numeric_dtype(df[col])
                or pd.api.types.is_datetime64_any_dtype(df[col])
                else "left"
            )
            new_row += f'<td style="text-align:{align}">' + cell

        new_rows.append(new_row)

    styled_table = "<tr>".join(new_rows).strip()

    # Filters block — plain, no styling
    if filters_used:
        filter_items = "".join(
            f"<li><b>{k}</b>: {v}</li>"
            for k, v in filters_used.items()
        )
        filters_html = f"""
<div>
    <p><b>Filters Used</b></p>
    <ul>
        {filter_items}
    </ul>
</div>"""
    else:
        filters_html = "<p><b>Filters Used:</b> None</p>"

    # Table first, filters below
    return styled_table + filters_html