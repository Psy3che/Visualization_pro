from datetime import date
from html import escape
import math
import json
from pathlib import Path

import dash
from dash import Input, Output, State, dash_table, dcc, html
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ROOT = Path(__file__).parent
DB_PATH = ROOT / "Data" / "player_data.duckdb"
DATA_READY = DB_PATH.exists()
INK, LIME, PAPER = "#19352b", "#c6ef5a", "#f3f5ef"
MUTED, GREEN, ORANGE, BLUE = "#68756e", "#71983b", "#e8794b", "#4e7b83"
COLORS = [GREEN, ORANGE, BLUE, "#bdc973", "#dfb056", "#927d68"]
GRAPH_CONFIG = {"displayModeBar": False}
METRICS = {
    "goals": "Goals", "assists": "Assists", "minutes_played": "Minutes",
    "appearances": "Appearances", "clean_sheets": "Clean sheets",
    "yellow_cards": "Yellow cards", "red_cards": "Red cards",
    "goals_per_90": "Goals / 90", "assists_per_90": "Assists / 90",
}
METRIC_SQL = {
    "goals": "SUM(COALESCE(f.goals, 0))",
    "assists": "SUM(COALESCE(f.assists, 0))",
    "minutes_played": "SUM(COALESCE(f.minutes_played, 0))",
    "appearances": "SUM(COALESCE(f.appearances, 0))",
    "clean_sheets": "SUM(COALESCE(f.clean_sheets, 0))",
    "yellow_cards": "SUM(COALESCE(f.yellow_cards, 0))",
    "red_cards": "SUM(COALESCE(f.red_cards, 0))",
    "goals_per_90": "90.0 * SUM(COALESCE(f.goals, 0)) / NULLIF(SUM(COALESCE(f.minutes_played, 0)), 0)",
    "assists_per_90": "90.0 * SUM(COALESCE(f.assists, 0)) / NULLIF(SUM(COALESCE(f.minutes_played, 0)), 0)",
}


def db():
    return duckdb.connect(str(DB_PATH), read_only=True)


def frame(sql, params=()):
    with db() as connection:
        return connection.execute(sql, params).df()


def scalar(sql, params=()):
    with db() as connection:
        return connection.execute(sql, params).fetchone()[0]


def records(data):
    if data.empty:
        return []
    return json.loads(data.to_json(orient="records", date_format="iso"))


def number(value):
    return f"{int(value or 0):,}"


def numeric_or_zero(value, as_float=False):
    if value is None or pd.isna(value):
        return 0.0 if as_float else 0
    return float(value) if as_float else int(value)


def euro(value):
    if value is None or pd.isna(value):
        return "N/A"
    value = float(value)
    for limit, divisor, suffix in ((1e9, 1e9, "bn"), (1e6, 1e6, "m"), (1e3, 1e3, "k")):
        if abs(value) >= limit:
            return f"€{value / divisor:.1f}{suffix}"
    return f"€{value:,.0f}"


def season_key(value):
    start = str(value or "").split("/")[0]
    try:
        year = int(start)
    except ValueError:
        return -1
    if len(start) < 4:
        return 1900 + year if year >= 90 else 2000 + year
    return year


def empty_figure(message):
    figure = go.Figure()
    figure.add_annotation(text=message, x=.5, y=.5, xref="paper", yref="paper", showarrow=False, font={"color": MUTED})
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    figure.update_layout(template="plotly_white", height=370, margin={"l": 12, "r": 12, "t": 12, "b": 12})
    return figure


def finish(figure, height=370):
    figure.update_layout(template="plotly_white", height=height, margin={"l": 12, "r": 12, "t": 18, "b": 30},
                         font={"family": "Trebuchet MS, Segoe UI, sans-serif", "color": INK}, paper_bgcolor="white",
                         plot_bgcolor="white", transition={"duration": 420, "easing": "cubic-in-out"})
    return figure


def where_for(season, competition, position, player_ids):
    clauses, values = [], []
    if season:
        clauses.append("f.season = ?")
        values.append(season)
    if competition:
        clauses.append("f.competition_name = ?")
        values.append(competition)
    if position:
        clauses.append("p.main_position = ?")
        values.append(position)
    selected_ids = list(dict.fromkeys(str(value) for value in (player_ids or []) if str(value).isdigit()))
    if selected_ids:
        placeholders = ", ".join("?" for _ in selected_ids)
        clauses.append(f"p.player_id IN ({placeholders})")
        values.extend(int(value) for value in selected_ids)
    return (" AND " + " AND ".join(clauses) if clauses else ""), values


def player_aggregates(season, competition, position, player_ids):
    where, params = where_for(season, competition, position, player_ids)
    data = frame(f"""
        SELECT p.player_id, SPLIT_PART(MAX(p.player_name), ' (', 1) AS player_name,
          MAX(p.main_position) AS main_position, MAX(p.citizenship) AS citizenship,
          MAX(p.current_club) AS current_club, SUM(COALESCE(f.appearances,0)) AS appearances,
          SUM(COALESCE(f.minutes_played,0)) AS minutes_played, SUM(COALESCE(f.goals,0)) AS goals,
          SUM(COALESCE(f.assists,0)) AS assists, SUM(COALESCE(f.clean_sheets,0)) AS clean_sheets,
          SUM(COALESCE(f.yellow_cards,0)) AS yellow_cards, SUM(COALESCE(f.red_cards,0)) AS red_cards
        FROM performance f JOIN profile p USING (player_id) WHERE 1=1 {where}
        GROUP BY p.player_id
    """, params)
    if not data.empty:
        minutes = data["minutes_played"].where(data["minutes_played"] > 0)
        data["goals_per_90"] = (data["goals"] * 90 / minutes).fillna(0).astype(float).round(2)
        data["assists_per_90"] = (data["assists"] * 90 / minutes).fillna(0).astype(float).round(2)
    return data


def table(table_id, columns, page_size=12):
    return dcc.Loading(
        dash_table.DataTable(
            id=table_id,
            columns=[
                {
                    "name": name,
                    "id": key,
                    "type": "numeric" if numeric else "text",
                    **({"format": {"nully": "-"}} if numeric else {}),
                }
                for name, key, numeric in columns
            ],
            data=[], page_action="native", page_size=page_size, sort_action="native",
            style_table={"overflowX": "auto"},
            style_cell={"fontFamily": "Trebuchet MS, Segoe UI, sans-serif", "fontSize": "12px", "padding": "9px",
                        "minWidth": "76px", "maxWidth": "230px", "whiteSpace": "normal", "textAlign": "left"},
            style_header={"backgroundColor": "#edf1e8", "color": INK, "fontWeight": "700", "border": "0"},
            style_data={"border": "0", "borderBottom": "1px solid #edf0ea"},
        ),
        type="circle", color=GREEN, delay_show=250, delay_hide=120,
        overlay_style={"visibility": "visible", "backgroundColor": "rgba(255,255,255,0.62)"},
    )


def card(title, graph_id, height=370):
    graph = dcc.Graph(id=graph_id, config=GRAPH_CONFIG, style={"height": f"{height}px"})
    loading_graph = dcc.Loading(graph, type="circle", color=GREEN, delay_show=250, delay_hide=120)
    return html.Section([html.H3(title, style={"fontSize": "16px", "margin": "0 0 6px"}), loading_graph],
                        className="chart-panel",
                        style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px",
                               "padding": "15px", "minWidth": 0})


def kpi(title, component_id):
    return html.Div([html.Div(title, style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                     html.Div(id=component_id, style={"fontSize": "23px", "fontWeight": "700", "marginTop": "6px",
                                                      "overflowWrap": "anywhere"})], className="metric-tile",
                    style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px",
                           "padding": "14px", "minWidth": 0})


def network_role_group(role):
    """Classify the *listed* role for display; this is not a real match position."""
    role = str(role or "").lower()
    if any(token in role for token in ("goalkeeper", "goalie", "keeper")):
        return "Goalkeeper"
    if any(token in role for token in ("midfield", "midfielder")):
        return "Midfield"
    if any(token in role for token in ("defend", "defence", "defense", "back")):
        return "Defence"
    if any(token in role for token in ("attack", "forward", "striker", "wing")):
        return "Attack"
    return "Other"


NETWORK_COLORS = {
    "Attack": "#ffaf79",
    "Midfield": "#7dddc3",
    "Defence": "#83bff1",
    "Goalkeeper": "#f7d57b",
    "Other": "#c4b5f2",
}


def network_figure(payload, metric="goals"):
    """2D interactive radial network; no Three.js or additional dependencies."""
    payload = payload or {}
    player_name = str(payload.get("player") or "Selected player")
    player_role = str(payload.get("role") or "Unspecified")
    nodes = payload.get("nodes") or []
    metric = metric if metric in ("goals", "minutes") else "goals"
    figure = go.Figure()
    bg = "#10291f"

    # Consistent role groups make neighboring positions easier to recognize.
    nodes = sorted(nodes, key=lambda node: (
        list(NETWORK_COLORS).index(network_role_group(node.get("role"))),
        str(node.get("player") or "")
    ))
    if not nodes:
        figure.add_annotation(text="No teammate connections available", x=.5, y=.5,
                              xref="paper", yref="paper", showarrow=False,
                              font={"size": 17, "color": "#cedcd2"})
        figure.update_layout(height=520, paper_bgcolor=bg, plot_bgcolor=bg,
                             margin={"l": 10, "r": 10, "t": 20, "b": 10},
                             xaxis={"visible": False}, yaxis={"visible": False})
        return figure

    # Balanced concentric rings allow both a few and dozens of teammates.
    positions = []
    rings = []
    remaining = len(nodes)
    ring_index = 0
    while remaining:
        take = min(remaining, 12 + ring_index * 10)
        radius = 3.15 + ring_index * 2.45
        rings.append(radius)
        for i in range(take):
            theta = math.pi / 2 - (2 * math.pi * (i + .5 * (ring_index % 2)) / take)
            positions.append((radius * math.cos(theta), radius * math.sin(theta)))
        remaining -= take
        ring_index += 1

    max_radius = rings[-1]
    for radius in rings:
        figure.add_shape(type="circle", x0=-radius, y0=-radius,
                         x1=radius, y1=radius,
                         line={"color": "rgba(191,226,194,0.11)", "width": 1},
                         layer="below")

    values = [max(0.0, float(node.get("joint_goals") or 0) if metric == "goals"
                  else float(node.get("minutes") or 0)) for node in nodes]
    max_value = max(values, default=0) or 1.0

    # The edges represent *documented links with the selected player only*.
    # They do not imply links between teammates.
    edge_x, edge_y = [], []
    for x, y in positions:
        edge_x.extend([0, x, None])
        edge_y.extend([0, y, None])
    figure.add_trace(go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line={"color": "rgba(198,239,90,0.30)", "width": 1.8},
        hoverinfo="skip", showlegend=False,
    ))

    for role, color in NETWORK_COLORS.items():
        indices = [i for i, node in enumerate(nodes) if network_role_group(node.get("role")) == role]
        if not indices:
            continue
        x = [positions[i][0] for i in indices]
        y = [positions[i][1] for i in indices]
        sizes = [18 + 27 * math.sqrt(values[i] / max_value) for i in indices]
        hover_text = []
        for i in indices:
            node = nodes[i]
            name = escape(str(node.get("player") or "Unknown"))
            listed_role = escape(str(node.get("role") or "Unspecified"))
            goals = "-" if node.get("joint_goals") is None else str(node["joint_goals"])
            ppg = "-" if node.get("ppg") is None else f"{float(node['ppg']):.2f}"
            mins = "-" if node.get("minutes") is None else f"{int(node['minutes']):,}"
            hover_text.append(
                f"<b>{name}</b><br>Listed role: {listed_role}"
                f"<br>Joint goals: {goals}<br>Points / game: {ppg}"
                f"<br>Minutes together: {mins}"
            )
        # Labels are shown for small networks; large networks remain legible on hover.
        show_labels = len(nodes) <= 14
        figure.add_trace(go.Scatter(
            x=x, y=y, name=role,
            mode="markers+text" if show_labels else "markers",
            text=[escape(str(nodes[i].get("player") or "Unknown")) for i in indices] if show_labels else None,
            textposition="top center", textfont={"size": 11, "color": "#f1f6ec"},
            cliponaxis=False,
            marker={"size": sizes, "color": color, "opacity": 0.96,
                    "line": {"color": bg, "width": 2}},
            hovertext=hover_text,
            hovertemplate="%{hovertext}<extra></extra>",
        ))

    # Distinct central player with a subtle halo.
    figure.add_trace(go.Scatter(
        x=[0], y=[0], mode="markers+text", name="Selected player",
        text=[escape(player_name)], textposition="bottom center",
        textfont={"size": 14, "color": "#f8ffee"},
        marker={"size": 65, "color": LIME,
                "line": {"color": "#e8fabc", "width": 5}},
        hovertext=[f"<b>{escape(player_name)}</b><br>Listed role: {escape(player_role)}<br>Selected player"],
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
    ))

    limit = max_radius + (2.2 if len(nodes) <= 14 else 1.2)
    metric_label = "Joint goals" if metric == "goals" else "Minutes together"
    figure.update_layout(
        height=570, paper_bgcolor=bg, plot_bgcolor=bg,
        font={"family": "Trebuchet MS, Segoe UI, sans-serif", "color": "#f3f8ed"},
        margin={"l": 42, "r": 42, "t": 72, "b": 55},
        hovermode="closest",
        hoverlabel={"bgcolor": "#f6f8f2", "font": {"color": INK, "size": 13}},
        legend={"orientation": "h", "x": .5, "xanchor": "center", "y": -.075,
                "yanchor": "top", "font": {"size": 11}},
        xaxis={"range": [-limit, limit], "visible": False, "fixedrange": False,
               "zeroline": False, "showgrid": False},
        yaxis={"range": [-limit, limit], "visible": False, "fixedrange": False,
               "zeroline": False, "showgrid": False},
        annotations=[
            {"text": f"<b>{escape(player_name)}</b>  ·  {len(nodes)} teammate connections",
             "xref": "paper", "yref": "paper", "x": 0, "y": 1.13, "showarrow": False,
             "xanchor": "left", "font": {"size": 16, "color": "#f5f9ef"}},
            {"text": f"Node size: {metric_label}  ·  Hover to inspect  ·  Scroll to zoom",
             "xref": "paper", "yref": "paper", "x": 1, "y": 1.13, "showarrow": False,
             "xanchor": "right", "font": {"size": 11, "color": "#b3c8b6"}},
        ],
        uirevision=f"2d-network-{player_name}-{len(nodes)}",
    )
    return figure


def startup():
    if not DATA_READY:
        return [], [], [], [], None, 0
    with db() as connection:
        seasons = [row[0] for row in
                   connection.execute("SELECT DISTINCT season FROM performance WHERE season IS NOT NULL").fetchall()]
        seasons.sort(key=season_key, reverse=True)
        competitions = connection.execute(
            "SELECT competition_name,COUNT(*) n FROM performance WHERE competition_name IS NOT NULL GROUP BY 1 ORDER BY n DESC,competition_name").fetchall()
        positions = [row[0] for row in connection.execute(
            "SELECT DISTINCT main_position FROM profile WHERE main_position IS NOT NULL ORDER BY 1").fetchall()]
        players = connection.execute("""
            WITH latest AS (SELECT player_id,ARG_MAX(value_eur,value_date) AS latest_value FROM market_values WHERE value_eur>0 GROUP BY player_id)
            SELECT p.player_id,SPLIT_PART(p.player_name,' (',1),p.citizenship FROM latest m JOIN profile p USING(player_id)
            ORDER BY m.latest_value DESC LIMIT 120
        """).fetchall()
        total = sum(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in (
        "player_profiles", "player_performances", "player_market_values", "player_injuries",
        "player_national_performances", "player_teammates", "player_transfers", "team_details",
        "team_competitions_seasons", "team_children"))
    options = [{"label": f"{name} · {country or 'Unknown'}", "value": str(player_id)} for player_id, name, country in
               players]
    return seasons, competitions, positions, options, (options[0]["value"] if options else None), total


SEASONS, COMPETITIONS, POSITIONS, PLAYER_OPTIONS, DEFAULT_PLAYER, ROW_COUNT = startup()
DEFAULT_SEASON = next((season for season in SEASONS if season == "23/24"), SEASONS[0] if SEASONS else None)
app = dash.Dash(__name__)
app.title = "Touchline | Player Intelligence"
server = app.server

PANEL = {"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "15px"}
PAGE = {"fontFamily": "'Trebuchet MS', 'Segoe UI', sans-serif", "backgroundColor": PAPER, "color": INK,
        "minHeight": "100vh", "paddingBottom": "35px"}

if not DATA_READY:
    app.layout = html.Main(style={**PAGE, "padding": "40px"}, children=[html.H1("Touchline: Player Intelligence"),
                                                                        html.P(
                                                                            "Build the local dataset before starting the dashboard."),
                                                                        html.Pre(
                                                                            "pip install -r requirements.txt\npython setup_dataset.py\npython app.py")])
else:
    app.layout = html.Div(style=PAGE, children=[
        html.Header(style={"backgroundColor": INK, "color": "white", "padding": "24px 5vw"}, children=[
            html.Div(style={"maxWidth": "1400px", "margin": "auto"}, children=[
                html.Div("TOUCHLINE  /  PLAYER INTELLIGENCE",
                         style={"color": LIME, "fontSize": "11px", "fontWeight": "700"}),
                html.H1("Global football, player by player", style={"fontSize": "32px", "margin": "8px 0"}),
                html.P(f"{ROW_COUNT:,} records across profiles, seasons, clubs, transfers, valuations and availability",
                       style={"margin": 0, "color": "#d3ddd5"})])]),
        html.Main(style={"maxWidth": "1400px", "margin": "18px auto", "padding": "0 16px"}, children=[
            html.Section(style={**PANEL, "display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(175px,1fr))",
                                "gap": "12px", "alignItems": "end", "marginBottom": "12px"}, children=[
                html.Div([html.Label("SEASON", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                          dcc.Dropdown(id="season-filter", options=[{"label": s, "value": s} for s in SEASONS],
                                       value=DEFAULT_SEASON, clearable=True, placeholder="All seasons")]),
                html.Div([html.Label("COMPETITION", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                          dcc.Dropdown(id="competition-filter",
                                       options=[{"label": name, "value": name} for name, _ in COMPETITIONS],
                                       searchable=True, clearable=True, placeholder="All competitions")]),
                html.Div([html.Label("POSITION", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                          dcc.Dropdown(id="position-filter",
                                       options=[{"label": value, "value": value} for value in POSITIONS],
                                       clearable=True, placeholder="All positions")]),
                html.Div([html.Label("PLAYER SEARCH", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                          dcc.Dropdown(id="player-search", options=PLAYER_OPTIONS, value=[], multi=True,
                                       searchable=True, clearable=True, placeholder="Type a name, then select")]),
                html.Div([html.Label("RANK BY", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                          dcc.Dropdown(id="metric-filter",
                                       options=[{"label": label, "value": key} for key, label in METRICS.items()],
                                       value="goals", clearable=False)]),
            ]),

            # 严格并列的多标签页导航结构
            dcc.Tabs(id="dashboard-tabs", value="performance", children=[
                # 1. Performance 标签页（含上方两列并排图表 + 底部全球地图）
                dcc.Tab(label="Performance", value="performance", children=[
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(170px,1fr))",
                                        "gap": "10px", "margin": "12px 0"},
                                 children=[kpi("PLAYERS", "players-count"), kpi("GOALS", "goals-total"),
                                           kpi("ASSISTS", "assists-total"), kpi("MINUTES", "minutes-total")]),
                    html.Div(id="metric-leader", style={"fontSize": "13px", "color": MUTED, "padding": "2px 0 10px"}),

                    # 上方两张并排图表 (1fr 1fr 布局)
                    html.Section(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"}, children=[
                        card("Player leaderboard", "metric-leaderboard"),
                        card("Goals and assists", "goal-assist-scatter"),
                        html.Div(card("Across-season trend", "season-trend"), style={"gridColumn": "1 / -1"})
                    ]),

                    # 底部全球球员分布地图组件
                    dcc.Loading(
                        html.Section(style={**PANEL, "marginTop": "12px"}, children=[
                            html.Div([
                                html.H3("Global Player Distribution Map", style={"fontSize": "16px", "margin": 0}),
                                html.Div("Geographic distribution of player performance",
                                         style={"fontSize": "12px", "color": MUTED})
                            ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                                      "marginBottom": "10px"}),
                            dcc.Graph(id="performance-map-chart", config=GRAPH_CONFIG, style={"height": "500px"})
                        ]),
                        type="circle", color=GREEN, delay_show=250, delay_hide=120,
                        overlay_style={"visibility": "visible", "backgroundColor": "rgba(255,255,255,0.62)"}
                    ),
                ]),

                # 2. Player 360 + 3D 标签页
                dcc.Tab(label="Player 360 + 2D", value="player", children=[
                    html.Section(style={**PANEL, "display": "grid",
                                        "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,250px),1fr))",
                                        "gap": "14px", "alignItems": "center", "margin": "12px 0"}, children=[html.Div(
                        [html.Label("PLAYER", style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}),
                         dcc.Dropdown(id="player-lookup", options=PLAYER_OPTIONS, value=DEFAULT_PLAYER, searchable=True,
                                      placeholder="Search name...")]), html.Div(id="player-identity")]),
                    html.Div(id="player-kpis",
                             style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(145px,1fr))",
                                    "gap": "9px", "marginBottom": "12px"}),
                    html.Section(
                        style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                               "gap": "12px"}, children=[card("Market value history", "player-market"),
                                                         card("Season-by-season output", "player-seasons"),
                                                         card("Injury days by cause", "player-injuries")]),
                    html.Section(
                        style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                               "gap": "12px", "marginTop": "12px"}, children=[html.Div(
                            [html.H3("Transfer history", style={"fontSize": "16px"}), table("player-transfers", [
                                ("Date", "transfer_date", False), ("From", "from_team", False),
                                ("To", "to_team", False), ("Fee", "transfer_fee", False)])], style=PANEL), html.Div(
                            [html.H3("Teammate connections", style={"fontSize": "16px"}), table("player-teammates", [
                                ("Player", "teammate_name", False), ("Listed role", "role", False),
                                ("Joint goals", "joint_goal_participation", True),
                                ("Points / game", "points_per_game", True),
                                ("Minutes together", "minutes_played_together", True)])], style=PANEL)]),
                    html.Section(style={**PANEL, "marginTop": "12px"}, children=[
                        html.Div([
                            html.Div([
                                html.H3("2D teammate network", style={"fontSize": "16px", "margin": "0"}),
                                html.P("Player connections by listed role. Hover to inspect, scroll to zoom.",
                                       style={"fontSize": "12px", "color": MUTED,
                                              "margin": "5px 0 0"}),
                            ]),
                            html.Div([
                                html.Label("NODE SIZE", style={"fontSize": "10px", "fontWeight": "700",
                                                               "color": MUTED, "marginRight": "12px"}),
                                dcc.RadioItems(
                                    id="network-metric", value="goals", inline=True,
                                    options=[{"label": "Joint goals", "value": "goals"},
                                             {"label": "Minutes together", "value": "minutes"}],
                                    inputStyle={"marginRight": "5px"},
                                    labelStyle={"marginRight": "16px", "cursor": "pointer",
                                                "fontSize": "12px"},
                                ),
                            ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"}),
                        ], style={"display": "flex", "justifyContent": "space-between",
                                  "alignItems": "center", "gap": "12px", "flexWrap": "wrap",
                                  "marginBottom": "12px"}),
                        dcc.Store(id="player-network-data"),
                        dcc.Loading(
                            dcc.Graph(
                                id="player-network", figure=network_figure({}),
                                config={"displayModeBar": False, "scrollZoom": True,
                                        "responsive": True, "doubleClick": "reset"},
                                style={"width": "100%", "height": "570px"},
                            ),
                            type="circle", color=GREEN, delay_show=250, delay_hide=120,
                        ),
                    ]),
                ]),

                # 3. Market & transfers 标签页
                dcc.Tab(label="Market & transfers", value="market", children=[html.Section(
                    style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(180px,1fr))",
                           "gap": "10px", "margin": "12px 0"},
                    children=[kpi("PLAYERS VALUED", "valued-count"), kpi("TRANSFER RECORDS", "transfer-count"),
                              kpi("TOP VALUE", "market-leader"), kpi("TOP DESTINATION", "transfer-destination")]),
                                                                              html.Section(style={"display": "grid",
                                                                                                  "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                                                                                                  "gap": "12px"},
                                                                                           children=[card(
                                                                                               "Highest current valuations",
                                                                                               "market-top"), card(
                                                                                               "Median recorded value over time",
                                                                                               "market-history"), card(
                                                                                               "Transfer activity by year",
                                                                                               "transfer-years"), card(
                                                                                               "Most frequent destinations",
                                                                                               "transfer-clubs")])]),

                # 4. Injuries & availability 标签页
                dcc.Tab(label="Injuries & availability", value="injuries", children=[
                    html.Section(style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(180px,1fr))",
                                        "gap": "10px", "margin": "12px 0"},
                                 children=[kpi("INJURY RECORDS", "injury-count"), kpi("DAYS MISSED", "days-missed"),
                                           kpi("GAMES MISSED", "games-missed"),
                                           kpi("PLAYERS AFFECTED", "players-injured")]),
                    html.Section(
                        style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                               "gap": "12px"},
                        children=[card("Days missed by injury type", "injury-types"),
                                  card("Availability by year", "injury-history")]),
                    # 【优化】：将原本的表格替换为了可视化图表卡片
                    html.Section(
                        style={"display": "grid", "gridTemplateColumns": "1fr", "gap": "12px", "marginTop": "12px"},
                        children=[card("Longest player absences (Top 15)", "injury-longest-chart", height=420)])
                ]),
                # 5. National & clubs 标签页
             ]),
            html.Footer(["Source: ",
                         html.A("salimt/football-datasets", href="https://github.com/salimt/football-datasets",
                                target="_blank", style={"color": INK}),
                         " · Transfermarkt-derived data. Verify source usage rights before redistribution."],
                        style={"fontSize": "11px", "color": MUTED, "padding": "16px 2px"}),
        ]),
    ])

if DATA_READY:
    @app.callback(
        Output("player-search", "options"),
        Input("player-search", "search_value"),
        State("player-search", "value"),
    )
    def search_filter_players(search_value, selected_ids):
        if search_value and len(search_value.strip()) >= 2:
            pattern = f"%{search_value.strip()}%"
            matches = frame("""
                SELECT player_id, SPLIT_PART(player_name, ' (', 1) AS player_name, citizenship
                FROM profile
                WHERE player_name ILIKE ? OR COALESCE(full_name, '') ILIKE ?
                ORDER BY player_name LIMIT 40
            """, [pattern, pattern])
            options = [{"label": f"{row.player_name} · {row.citizenship or 'Unknown'}", "value": str(row.player_id)} for
                       row in matches.itertuples()]
        else:
            options = PLAYER_OPTIONS

        selected_ids = list(dict.fromkeys(str(value) for value in (selected_ids or []) if str(value).isdigit()))
        missing_ids = [value for value in selected_ids if not any(option["value"] == value for option in options)]
        if missing_ids:
            placeholders = ", ".join("?" for _ in missing_ids)
            selected = frame(f"""
                SELECT player_id, SPLIT_PART(player_name, ' (', 1) AS player_name, citizenship
                FROM profile WHERE player_id IN ({placeholders})
            """, [int(value) for value in missing_ids])
            options = [{"label": f"{row.player_name} · {row.citizenship or 'Unknown'}", "value": str(row.player_id)} for
                       row in selected.itertuples()] + options
        return options


    # 1. Performance 页面上方图表与 KPI 回调
    @app.callback(
        Output("players-count", "children"), Output("goals-total", "children"), Output("assists-total", "children"),
        Output("minutes-total", "children"),
        Output("metric-leader", "children"), Output("metric-leaderboard", "figure"),
        Output("goal-assist-scatter", "figure"), Output("season-trend", "figure"),
        Input("season-filter", "value"), Input("competition-filter", "value"), Input("position-filter", "value"),
        Input("player-search", "value"), Input("metric-filter", "value"),
    )
    def update_performance(season, competition, position, player_ids, metric):
        metric = metric if metric in METRICS else "goals"
        data = player_aggregates(season, competition, position, player_ids)
        if data.empty:
            empty = empty_figure("No players match these filters")
            return "0", "0", "0", "0", "No matching players", empty, empty, empty
        data[metric] = pd.to_numeric(data[metric], errors="coerce").fillna(0)
        data = data.sort_values([metric, "goals", "minutes_played"], ascending=False)
        leader = data.iloc[0]
        suffix = f"{leader[metric]:,.2f}" if metric.endswith("per_90") else f"{leader[metric]:,.0f}"
        leader_text = f"Top {METRICS[metric].lower()}: {leader['player_name']} · {suffix}"
        top = data.nlargest(14, metric).sort_values(metric)
        ranking = px.bar(top, x=metric, y="player_name", orientation="h", text=metric, color=metric,
                         color_continuous_scale=["#dce8c6", GREEN],
                         labels={metric: METRICS[metric], "player_name": "Player"},
                         hover_data={"main_position": True, "citizenship": True, "appearances": True})
        ranking.update_traces(textposition="outside")
        ranking.update_layout(showlegend=False, coloraxis_showscale=False, xaxis_rangemode="tozero", yaxis_title=None)
        finish(ranking)
        scatter = px.scatter(data.nlargest(min(700, len(data)), "goals"), x="goals", y="assists", color="main_position",
                             size="minutes_played", hover_name="player_name",
                             hover_data={"citizenship": True, "current_club": True, "goals_per_90": ":.2f",
                                         "assists_per_90": ":.2f"}, color_discrete_sequence=COLORS,
                             labels={"goals": "Goals", "assists": "Assists", "main_position": "Position",
                                     "minutes_played": "Minutes"})
        finish(scatter)
        trend_where, trend_params = where_for(None, competition, position, player_ids)
        trend = frame(
            f"SELECT f.season,{METRIC_SQL[metric]} AS metric_value FROM performance f JOIN profile p USING(player_id) WHERE f.season IS NOT NULL {trend_where} GROUP BY f.season",
            trend_params)
        if trend.empty:
            trend_figure = empty_figure("No season history")
        else:
            trend["order"] = trend["season"].map(season_key)
            trend = trend.sort_values("order")
            trend_figure = px.line(trend, x="season", y="metric_value", markers=True,
                                   labels={"season": "Season", "metric_value": METRICS[metric]})
            trend_figure.update_traces(line={"color": GREEN, "width": 3})
            finish(trend_figure, 350)
        return number(len(data)), number(data["goals"].sum()), number(data["assists"].sum()), number(
            data["minutes_played"].sum()), leader_text, ranking, scatter, trend_figure


    # 2. Performance 页面下方全球地图回调
    @app.callback(
        Output("performance-map-chart", "figure"),
        [
            Input("season-filter", "value"),
            Input("competition-filter", "value"),
            Input("position-filter", "value"),
            Input("player-search", "value"),
        ],
    )
    def update_performance_map(season, competition, position, player_ids):
        df = player_aggregates(season, competition, position, player_ids)
        if df.empty or "citizenship" not in df.columns:
            return empty_figure("No geographical data available for current filters.")

        map_df = df.groupby("citizenship").agg(
            player_count=("player_id", "count"),
            total_goals=("goals", "sum")
        ).reset_index()

        fig = px.choropleth(
            map_df,
            locations="citizenship",
            locationmode="country names",
            color="total_goals",
            hover_name="citizenship",
            hover_data=["player_count", "total_goals"],
            color_continuous_scale=["#edf1e8", GREEN, INK],
            labels={"total_goals": "Total Goals", "player_count": "Players"}
        )
        return finish(fig, height=500)


    @app.callback(Output("player-lookup", "options"), Input("player-lookup", "search_value"),
                  State("player-lookup", "value"))
    def player_search(search, selected):
        if search and len(search.strip()) >= 2:
            pattern = f"%{search.strip()}%"
            matches = frame(
                "SELECT player_id,SPLIT_PART(player_name,' (',1) player_name,citizenship FROM profile WHERE player_name ILIKE ? OR COALESCE(full_name,'') ILIKE ? ORDER BY player_name LIMIT 40",
                [pattern, pattern])
            options = [{"label": f"{r.player_name} · {r.citizenship or 'Unknown'}", "value": str(r.player_id)} for r in
                       matches.itertuples()]
        else:
            options = PLAYER_OPTIONS
        if selected and not any(item["value"] == str(selected) for item in options):
            row = frame(
                "SELECT player_id,SPLIT_PART(player_name,' (',1) player_name,citizenship FROM profile WHERE player_id=TRY_CAST(? AS BIGINT)",
                [selected])
            if not row.empty:
                r = row.iloc[0]
                options.insert(0,
                               {"label": f"{r.player_name} · {r.citizenship or 'Unknown'}", "value": str(r.player_id)})
        return options


    @app.callback(
        Output("player-identity", "children"), Output("player-kpis", "children"), Output("player-market", "figure"),
        Output("player-seasons", "figure"),
        Output("player-injuries", "figure"), Output("player-transfers", "data"), Output("player-teammates", "data"),
        Output("player-network-data", "data"),
        Input("player-lookup", "value"),
    )
    def update_player(player_id):
        if not player_id:
            blank = empty_figure("Select a player")
            return "Choose a player", [], blank, blank, blank, [], [], {"player": "Select a player", "nodes": []}
        profile = frame("SELECT * FROM profile WHERE player_id=TRY_CAST(? AS BIGINT)", [player_id])
        if profile.empty:
            blank = empty_figure("Player not found")
            return "Player not found", [], blank, blank, blank, [], [], {"player": "Player not found", "nodes": []}
        player = profile.iloc[0]
        dob = pd.to_datetime(player["date_of_birth"], errors="coerce")
        age = int((pd.Timestamp(date.today()) - dob).days / 365.2425) if not pd.isna(dob) else None
        facts = [value for value in [player["current_club"], player["citizenship"], player["main_position"]] if
                 value and not pd.isna(value)]
        physical_facts = [value for value in [f"{age} yrs" if age else None, player["height"], player["foot"]] if
                          value and not pd.isna(value)]
        identity = html.Div([
            html.Img(src=player["player_image_url"], alt=player["player_name"],
                     style={"width": "70px", "height": "70px", "objectFit": "cover", "borderRadius": "50%",
                            "backgroundColor": "#edf1e8"}) if player["player_image_url"] else html.Div(
                style={"width": "70px", "height": "70px", "borderRadius": "50%", "backgroundColor": LIME}),
            html.Div([
                html.H2(str(player["player_name"]).split(" (")[0], style={"fontSize": "22px", "margin": "0 0 3px"}),
                html.Div(" · ".join(map(str, facts)), style={"fontSize": "12px", "color": MUTED}),
                html.Div(" · ".join(map(str, physical_facts)),
                         style={"fontSize": "11px", "color": MUTED, "marginTop": "4px"})
            ])
        ], style={"display": "flex", "alignItems": "center", "gap": "14px"})
        career = frame(
            "SELECT COUNT(DISTINCT season) seasons,SUM(COALESCE(goals,0)) goals,SUM(COALESCE(assists,0)) assists,SUM(COALESCE(appearances,0)) appearances,SUM(COALESCE(minutes_played,0)) AS total_minutes FROM performance WHERE player_id=TRY_CAST(? AS BIGINT)",
            [player_id]).iloc[0]
        latest_value = scalar("SELECT MAX(value_eur) FROM market_values WHERE player_id=TRY_CAST(? AS BIGINT)",
                              [player_id])
        caps = scalar("SELECT SUM(matches) FROM national_performances WHERE player_id=TRY_CAST(? AS BIGINT)",
                      [player_id])
        kpis = [html.Div([html.Div(label, style={"fontSize": "9px", "fontWeight": "700", "color": MUTED}),
                          html.Div(value, style={"fontSize": "18px", "fontWeight": "700", "marginTop": "4px"})],
                         style=PANEL) for label, value in
                [("SEASONS", number(career["seasons"])), ("GOALS", number(career["goals"])),
                 ("ASSISTS", number(career["assists"])), ("APPEARANCES", number(career["appearances"])),
                 ("LATEST VALUE", euro(latest_value)), ("NATIONAL CAPS", number(caps))]]
        market = frame(
            "SELECT value_date,MAX(value_eur) AS market_value_eur FROM market_values WHERE player_id=TRY_CAST(? AS BIGINT) AND value_date IS NOT NULL GROUP BY value_date ORDER BY value_date",
            [player_id])
        if market.empty:
            market_fig = empty_figure("No valuation history")
        else:
            market["value_m"] = market["market_value_eur"] / 1e6
            market_fig = px.line(market, x="value_date", y="value_m", markers=True,
                                 labels={"value_date": "Date", "value_m": "Market value (€m)"})
            market_fig.update_traces(line={"color": GREEN, "width": 3})
            finish(market_fig)
        seasons = frame(
            "SELECT season,SUM(COALESCE(goals,0)) goals,SUM(COALESCE(assists,0)) assists FROM performance WHERE player_id=TRY_CAST(? AS BIGINT) GROUP BY season",
            [player_id])
        if seasons.empty:
            season_fig = empty_figure("No performance history")
        else:
            seasons["order"] = seasons["season"].map(season_key)
            long = seasons.sort_values("order").melt(id_vars=["season", "order"], value_vars=["goals", "assists"],
                                                     var_name="metric", value_name="total")
            season_fig = px.line(long, x="season", y="total", color="metric", markers=True,
                                 color_discrete_sequence=[GREEN, ORANGE],
                                 labels={"season": "Season", "total": "Total", "metric": ""})
            season_fig.update_layout(legend_title=None)
            finish(season_fig)
        injury_data = frame(
            "SELECT COALESCE(injury_reason,'Unspecified') reason,SUM(COALESCE(days_missed,0)) AS missed_days FROM injuries WHERE player_id=TRY_CAST(? AS BIGINT) GROUP BY 1 ORDER BY missed_days DESC LIMIT 10",
            [player_id])
        if injury_data.empty:
            injury_fig = empty_figure("No injury records")
        else:
            injury_fig = px.bar(injury_data.sort_values("missed_days"), x="missed_days", y="reason", orientation="h",
                                color="missed_days", color_continuous_scale=["#f1d7c7", ORANGE],
                                labels={"missed_days": "Days missed", "reason": "Injury"})
            injury_fig.update_layout(coloraxis_showscale=False, yaxis_title=None)
            finish(injury_fig)
        transfer_rows = frame(
            "SELECT CAST(transfer_date AS VARCHAR) transfer_date,from_team,to_team,transfer_fee FROM transfers WHERE player_id=TRY_CAST(? AS BIGINT) ORDER BY transfer_date DESC NULLS LAST LIMIT 100",
            [player_id])
        teammate_rows = frame("""
                     SELECT COALESCE(SPLIT_PART(p.player_name,' (',1),t.teammate_name) AS teammate_name,
                         COALESCE(p.sub_position,p.main_position) AS role,
                         p.main_position,t.joint_goal_participation,t.points_per_game,t.minutes_played_together
                FROM teammate_links t
                LEFT JOIN profile p ON p.player_id=t.teammate_id
                WHERE t.player_id=TRY_CAST(? AS BIGINT)
                ORDER BY t.joint_goal_participation DESC NULLS LAST,t.points_per_game DESC NULLS LAST
                LIMIT 100
            """, [player_id])
        # Keep missing numbers as None (not 0), so tooltips show "-" accurately.
        network_nodes = [{
            "player": str(row.teammate_name) if pd.notna(row.teammate_name) else "Unknown",
            "role": str(row.role) if pd.notna(row.role) else "Unspecified",
            "joint_goals": None if pd.isna(row.joint_goal_participation) else int(row.joint_goal_participation),
            "ppg": None if pd.isna(row.points_per_game) else float(row.points_per_game),
            "minutes": None if pd.isna(row.minutes_played_together) else int(row.minutes_played_together),
        } for row in teammate_rows.itertuples()]
        # Presentation-only cleanup: missing text fields in teammate table display as "-".
        # Numeric fields stay as None, so their native numeric sorting is preserved.
        teammate_display_rows = teammate_rows.copy()
        for column in ("teammate_name", "role"):
            teammate_display_rows[column] = (
                teammate_display_rows[column]
                .replace(r"^\s*$", pd.NA, regex=True)
                .fillna("-")
            )
        selected_role = next((str(value) for value in (player["sub_position"], player["main_position"]) if
                              value is not None and not pd.isna(value) and str(value).strip()), "Unspecified")
        return identity, kpis, market_fig, season_fig, injury_fig, records(transfer_rows), records(
            teammate_display_rows), {
                "player": str(player["player_name"]).split(" (")[0],
                "role": selected_role,
                "nodes": network_nodes,
            }
    @app.callback(
        Output("player-network", "figure"),
        Input("player-network-data", "data"),
        Input("network-metric", "value"),
    )
    def update_network(network_data, metric):
        return network_figure(network_data, metric)

    @app.callback(
        Output("valued-count", "children"), Output("transfer-count", "children"), Output("market-leader", "children"),
        Output("transfer-destination", "children"),
        Output("market-top", "figure"), Output("market-history", "figure"), Output("transfer-years", "figure"),
        Output("transfer-clubs", "figure"),
        Input("dashboard-tabs", "value"),
    )
    def update_market(tab):
        if tab != "market":
            blank = empty_figure("Open this view to load the data")
            return "—", "—", "—", "—", blank, blank, blank, blank
        latest = frame(
            "WITH v AS (SELECT player_id,ARG_MAX(value_eur,value_date) AS market_value_eur,MAX(value_date) AS value_date FROM market_values WHERE value_eur>0 GROUP BY player_id) SELECT SPLIT_PART(p.player_name,' (',1) player_name,p.main_position,v.market_value_eur,v.value_date FROM v JOIN profile p USING(player_id) ORDER BY v.market_value_eur DESC LIMIT 18")
        destination = frame(
            "SELECT to_team,COUNT(*) moves FROM transfers WHERE to_team IS NOT NULL GROUP BY 1 ORDER BY moves DESC LIMIT 12")
        if latest.empty:
            leader = "N/A";
            bar = empty_figure("No valuations")
        else:
            leader = f"{latest.iloc[0]['player_name']} · {euro(latest.iloc[0]['market_value_eur'])}"
            bar = px.bar(latest.sort_values("market_value_eur"), x="market_value_eur", y="player_name", orientation="h",
                         color="market_value_eur", color_continuous_scale=["#dce8c6", GREEN],
                         labels={"market_value_eur": "Value (€)", "player_name": "Player"},
                         hover_data={"main_position": True, "value_date": True});
            bar.update_layout(coloraxis_showscale=False, yaxis_title=None, xaxis_tickprefix="€");
            finish(bar)
        history = frame(
            "SELECT YEAR(value_date) AS calendar_year,QUANTILE_CONT(value_eur,0.5) AS median FROM market_values WHERE value_date IS NOT NULL AND value_eur>0 GROUP BY 1 ORDER BY 1")
        if history.empty:
            history_fig = empty_figure("No valuation timeline")
        else:
            history = history.tail(25);
            history["median_m"] = history["median"] / 1e6;
            history_fig = px.line(history, x="calendar_year", y="median_m", markers=True,
                                  labels={"calendar_year": "Year", "median_m": "Median value (€m)"});
            history_fig.update_traces(line={"color": BLUE, "width": 3});
            finish(history_fig)
        by_year = frame(
            "SELECT YEAR(transfer_date) AS calendar_year,COUNT(*) moves FROM transfers WHERE transfer_date IS NOT NULL GROUP BY 1 ORDER BY 1")
        years = px.area(by_year.tail(35), x="calendar_year", y="moves",
                        labels={"calendar_year": "Year", "moves": "Transfers"}) if not by_year.empty else empty_figure(
            "No dated transfers")
        if not by_year.empty:
            years.update_traces(line_color=ORANGE, fillcolor="rgba(232,121,75,.24)");
            finish(years)
        clubs = px.bar(destination.sort_values("moves"), x="moves", y="to_team", orientation="h", color="moves",
                       color_continuous_scale=["#f1d7c7", ORANGE], labels={"moves": "Records",
                                                                           "to_team": "Destination"}) if not destination.empty else empty_figure(
            "No destinations")
        if not destination.empty:
            clubs.update_layout(coloraxis_showscale=False, yaxis_title=None);
            finish(clubs)
        transfer_n = scalar("SELECT COUNT(*) FROM transfers")
        return number(len(latest)), number(transfer_n), leader, str(
            destination.iloc[0]["to_team"] if not destination.empty else "N/A"), bar, history_fig, years, clubs


    @app.callback(
        Output("injury-count", "children"),
        Output("days-missed", "children"),
        Output("games-missed", "children"),
        Output("players-injured", "children"),
        Output("injury-types", "figure"),
        Output("injury-history", "figure"),
        Output("injury-longest-chart", "figure"),
        Input("dashboard-tabs", "value"),
    )
    def update_injuries(tab):
        if tab != "injuries":
            blank = empty_figure("Open this view to load the data")
            return "—", "—", "—", "—", blank, blank, blank

        totals = frame(
            "SELECT COUNT(*) cases, SUM(COALESCE(days_missed,0)) AS missed_days, SUM(COALESCE(games_missed,0)) games, COUNT(DISTINCT player_id) players FROM injuries"
        ).iloc[0]

        reasons = frame(
            "SELECT COALESCE(injury_reason,'Unspecified') reason, SUM(COALESCE(days_missed,0)) AS missed_days, COUNT(*) cases FROM injuries GROUP BY 1 ORDER BY missed_days DESC LIMIT 12"
        )

        # 已补全 FROM injuries
        trend = frame(
            "SELECT YEAR(from_date) AS calendar_year, COUNT(*) cases, SUM(COALESCE(days_missed,0)) AS missed_days FROM injuries WHERE from_date IS NOT NULL GROUP BY 1 ORDER BY 1"
        )

        # 按球员和赛季合并累计缺阵天数，确保严格从高到低排列
        longest = frame("""
                    SELECT 
                        SPLIT_PART(p.player_name,' (',1) AS player_name, 
                        COALESCE(i.season, 'N/A') AS season,
                        STRING_AGG(DISTINCT COALESCE(i.injury_reason, 'Unspecified'), ', ') AS injury_reason,
                        SUM(COALESCE(i.days_missed, 0)) AS days_missed, 
                        SUM(COALESCE(i.games_missed, 0)) AS games_missed 
                    FROM injuries i JOIN profile p USING(player_id) 
                    WHERE i.days_missed IS NOT NULL 
                    GROUP BY 1, 2
                    ORDER BY days_missed DESC 
                    LIMIT 15
                """)

        if reasons.empty:
            rfig = empty_figure("No injuries")
        else:
            rfig = px.bar(
                reasons.sort_values("missed_days"),
                x="missed_days",
                y="reason",
                orientation="h",
                color="cases",
                color_continuous_scale=["#f1d7c7", ORANGE],
                labels={"missed_days": "Days missed", "reason": "Injury", "cases": "Cases"},
            )
            rfig.update_layout(coloraxis_showscale=False, yaxis_title=None)
            finish(rfig)

        if trend.empty:
            tfig = empty_figure("No injury dates")
        else:
            tfig = px.bar(
                trend.tail(30),
                x="calendar_year",
                y="missed_days",
                color="cases",
                color_continuous_scale="Tealgrn",
                labels={"calendar_year": "Year", "missed_days": "Days missed", "cases": "Injury records"},
            )
            tfig.update_layout(coloraxis_showscale=False)
            finish(tfig)

        if longest.empty:
            longest_fig = empty_figure("No long-term absence records")
        else:
            longest["display_label"] = longest["player_name"] + " (" + longest["season"].fillna("N/A") + ")"
            longest_plot = longest.sort_values("days_missed")
            if longest.empty:
                longest_fig = empty_figure("No long-term absence records")
            else:
                longest["display_label"] = longest["player_name"] + " (" + longest["season"] + ")"
                longest_plot = longest.sort_values("days_missed")
                max_days = float(longest_plot["days_missed"].max() or 1)

                longest_fig = px.bar(
                    longest_plot,
                    x="days_missed",
                    y="display_label",
                    orientation="h",
                    text="days_missed",  # 在柱子末端直接显示具体天数
                    color="days_missed",
                    color_continuous_scale=["#f1d7c7", ORANGE],
                    labels={"days_missed": "Days missed", "display_label": "Player (Season)"},
                    hover_data={"injury_reason": True, "games_missed": True, "display_label": False},
                )
                longest_fig.update_traces(textposition="outside")
                longest_fig.update_layout(
                    coloraxis_showscale=False,
                    yaxis_title=None,
                    yaxis={"categoryorder": "total ascending"},
                    # 锁定 X 轴范围为最大值的 1.08 倍，彻底消除右侧大片空白
                    xaxis={"range": [0, max_days * 1.08]},
                    height=420
                )
                finish(longest_fig, height=420)

        return (
            number(totals["cases"]),
            number(totals["missed_days"]),
            number(totals["games"]),
            number(totals["players"]),
            rfig,
            tfig,
            longest_fig,
        )

    @app.callback(Output("national-caps", "figure"), Output("national-goals", "figure"),
                  Output("club-points", "figure"), Output("club-table", "data"), Input("season-filter", "value"),
                  Input("competition-filter", "value"), Input("dashboard-tabs", "value"))
    def update_clubs(season, competition, tab):
        if tab != "teams":
            blank = empty_figure("Open this view to load the data")
            return blank, blank, blank, []
        national = frame(
            "SELECT p.player_id,SPLIT_PART(p.player_name,' (',1) player_name,MAX(p.citizenship) citizenship,SUM(COALESCE(n.matches,0)) caps,SUM(COALESCE(n.goals,0)) goals FROM national_performances n JOIN profile p USING(player_id) GROUP BY p.player_id,p.player_name")
        top = national.nlargest(16, "caps").sort_values("caps") if not national.empty else national
        cap_fig = px.bar(top, x="caps", y="player_name", orientation="h", color="caps",
                         color_continuous_scale=["#dce8c6", GREEN],
                         labels={"caps": "International appearances", "player_name": "Player"},
                         hover_data={"citizenship": True, "goals": True}) if not top.empty else empty_figure(
            "No national records")
        scatter = px.scatter(national.nlargest(min(700, len(national)), "caps"), x="caps", y="goals",
                             color="citizenship", hover_name="player_name",
                             labels={"caps": "Caps", "goals": "Goals", "citizenship": "Nationality"},
                             color_discrete_sequence=COLORS) if not national.empty else empty_figure(
            "No national records")
        if not top.empty: cap_fig.update_layout(coloraxis_showscale=False, yaxis_title=None);finish(cap_fig)
        if not national.empty: finish(scatter)
        clauses = ["points IS NOT NULL", "played > 0"]
        params = []
        if season:
            clauses.append("season=?")
            params.append(season)
        if competition:
            clauses.append("competition_name=?")
            params.append(competition)

        # 在 SQL 中直接计算净胜球 (GD) 和场均积分 (PPG)
        clubs = frame(
            f"""
                    SELECT *,
                           (COALESCE(goals_for, 0) - COALESCE(goals_against, 0)) AS goal_diff,
                           ROUND(CAST(points AS DOUBLE) / NULLIF(played, 0), 2) AS ppg
                    FROM club_seasons 
                    WHERE {' AND '.join(clauses)} 
                    ORDER BY {"rank ASC NULLS LAST, points DESC" if competition else "ppg DESC, goal_diff DESC"} 
                    LIMIT 500
                    """,
            params,
        )
        if clubs.empty:
            return cap_fig, scatter, empty_figure("No club seasons for these filters"), []

        # 图表改为按场均积分 (PPG) 对比，消除不同联赛赛程长度差异
        plot = clubs.head(18).sort_values("ppg")
        club_fig = px.bar(
            plot,
            x="ppg",
            y="team_name",
            orientation="h",
            color="competition_name",
            text="ppg",
            hover_data={"rank": True, "season": True, "points": True, "played": True, "goal_diff": True},
            labels={"ppg": "Points per game (PPG)", "team_name": "Club", "competition_name": "Competition"},
            color_discrete_sequence=COLORS,
        )
        club_fig.update_traces(textposition="outside")
        club_fig.update_layout(yaxis_title=None, legend_title=None, xaxis={"range": [0, 3.3]})
        finish(club_fig)
        return cap_fig, scatter, club_fig, records(clubs)

if __name__ == "__main__":
    app.run(debug=False)