from datetime import date
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
    figure.update_layout(template="plotly_white", height=height, margin={"l": 12, "r": 12, "t": 18, "b": 30}, font={"family": "Trebuchet MS, Segoe UI, sans-serif", "color": INK}, paper_bgcolor="white", plot_bgcolor="white", transition={"duration": 420, "easing": "cubic-in-out"})
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
            columns=[{"name": name, "id": key, "type": "numeric" if numeric else "text"} for name, key, numeric in columns],
            data=[], page_action="native", page_size=page_size, sort_action="native",
            style_table={"overflowX": "auto"},
            style_cell={"fontFamily": "Trebuchet MS, Segoe UI, sans-serif", "fontSize": "12px", "padding": "9px", "minWidth": "76px", "maxWidth": "230px", "whiteSpace": "normal", "textAlign": "left"},
            style_header={"backgroundColor": "#edf1e8", "color": INK, "fontWeight": "700", "border": "0"},
            style_data={"border": "0", "borderBottom": "1px solid #edf0ea"},
        ),
        type="circle", color=GREEN, delay_show=250, delay_hide=120,
        overlay_style={"visibility": "visible", "backgroundColor": "rgba(255,255,255,0.62)"},
    )


def card(title, graph_id, height=370):
    graph = dcc.Graph(id=graph_id, config=GRAPH_CONFIG, style={"height": f"{height}px"})
    loading_graph = dcc.Loading(graph, type="circle", color=GREEN, delay_show=250, delay_hide=120)
    return html.Section([html.H3(title, style={"fontSize": "16px", "margin": "0 0 6px"}), loading_graph], className="chart-panel", style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "15px", "minWidth": 0})


def kpi(title, component_id):
    return html.Div([html.Div(title, style={"fontSize": "10px", "fontWeight": "700", "color": MUTED}), html.Div(id=component_id, style={"fontSize": "23px", "fontWeight": "700", "marginTop": "6px", "overflowWrap": "anywhere"})], className="metric-tile", style={"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "14px", "minWidth": 0})


def network_document(payload):
    encoded = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
    *{{box-sizing:border-box}} body{{margin:0;background:#10251e;color:#f2f5ed;font-family:'Trebuchet MS',sans-serif;overflow:hidden}}
      #stage{{position:absolute;inset:0}} canvas{{display:block;width:100%;height:100%}}
      .top{{position:absolute;z-index:2;left:18px;top:15px;pointer-events:none}} .eyebrow{{font-size:10px;letter-spacing:1.5px;color:#c6ef5a;font-weight:700}}
      h2{{font-size:18px;margin:5px 0 3px}} .sub{{font-size:11px;color:#b4c4b7}} .tools{{position:absolute;z-index:3;right:14px;top:14px;display:flex;gap:6px}}
      button{{border:1px solid #486252;background:#1c392c;color:#eaf2e8;padding:7px 9px;border-radius:4px;font:11px inherit;cursor:pointer}} button[aria-pressed=true]{{background:#c6ef5a;color:#173228;border-color:#c6ef5a}}
      #tip{{display:none;position:absolute;z-index:4;min-width:150px;max-width:220px;padding:9px 11px;background:#f6f8f2;color:#19352b;border-radius:4px;font-size:11px;line-height:1.5;pointer-events:none;box-shadow:0 5px 18px #0005}}
      .legend{{position:absolute;left:16px;bottom:14px;font-size:10px;color:#c5d1c8;pointer-events:none}} .hint{{position:absolute;right:15px;bottom:14px;font-size:10px;color:#aebdb2;pointer-events:none}}
      @media(max-width:540px){{.top{{left:12px;top:12px}}h2{{font-size:15px}}.tools{{right:8px;top:8px;gap:4px}}button{{padding:6px;font-size:10px}}.hint{{display:none}}}}
    </style></head><body><div id="stage"></div><div class="top"><div class="eyebrow">TEAMMATE POSITION MAP</div><h2 id="player-name"></h2><div class="sub" id="network-meta"></div></div>
    <div class="tools"><button id="layout" aria-pressed="false">Top-down</button><button id="metric" aria-pressed="false">Joint goals</button><button id="reset">Reset view</button></div>
    <div class="legend">Listed role sets pitch zone · links show recorded teammate connections</div><div class="hint">Drag to orbit · wheel to zoom · click a player to inspect</div><div id="tip"></div>
    <script type="importmap">{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/"}}}}</script>
    <script type="module">
    import * as THREE from 'three'; import {{OrbitControls}} from 'three/addons/controls/OrbitControls.js';
        const data={encoded}; document.getElementById('player-name').textContent=data.player||'Player network';
        document.getElementById('network-meta').textContent=`${{data.nodes.length}} teammate links · ${{data.role||'role'}} zone · not a match lineup`;
        const host=document.getElementById('stage'), scene=new THREE.Scene(); scene.background=new THREE.Color('#163722');
        const camera=new THREE.PerspectiveCamera(44,1,.1,1200); camera.position.set(82,82,92);
        const renderer=new THREE.WebGLRenderer({{antialias:true,alpha:false,preserveDrawingBuffer:true}}); renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.outputColorSpace=THREE.SRGBColorSpace; host.appendChild(renderer.domElement);
        const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true; controls.dampingFactor=.055; controls.minDistance=75; controls.maxDistance=330; controls.maxPolarAngle=Math.PI*.49;
        scene.add(new THREE.HemisphereLight(0xe0f2d6,0x284331,2)); const key=new THREE.DirectionalLight(0xffffff,2.1); key.position.set(-65,110,55); scene.add(key);
        const root=new THREE.Group(); scene.add(root); const raycaster=new THREE.Raycaster(); const pointer=new THREE.Vector2(); const meshes=[]; const lineGroup=new THREE.Group(); root.add(lineGroup);
        const pitch=new THREE.Group(); root.add(pitch);
        const grass=new THREE.Mesh(new THREE.PlaneGeometry(150,96),new THREE.MeshStandardMaterial({{color:0x387a48,roughness:.95}})); grass.rotation.x=-Math.PI/2; grass.position.y=-.4; pitch.add(grass);
        function pitchLine(points,loop=false,opacity=.9){{const vertices=points.map(([x,z])=>new THREE.Vector3(x,.12,z));const geometry=new THREE.BufferGeometry().setFromPoints(vertices);const material=new THREE.LineBasicMaterial({{color:0xe0ead9,transparent:true,opacity}});pitch.add(loop?new THREE.LineLoop(geometry,material):new THREE.Line(geometry,material))}}
        function pitchRect(x1,x2,z1,z2){{pitchLine([[x1,z1],[x2,z1],[x2,z2],[x1,z2]],true)}}
        function zoneTag(label,x,z){{const canvas=document.createElement('canvas');canvas.width=512;canvas.height=128;const context=canvas.getContext('2d');context.fillStyle='rgba(18,43,27,.9)';context.beginPath();context.roundRect(8,8,496,112,18);context.fill();context.fillStyle='#f4f7ee';context.font='bold 40px Trebuchet MS, sans-serif';context.textAlign='center';context.textBaseline='middle';context.fillText(label,256,64);const texture=new THREE.CanvasTexture(canvas);const tag=new THREE.Sprite(new THREE.SpriteMaterial({{map:texture,transparent:true,depthWrite:false}}));tag.position.set(x,.55,z);tag.scale.set(29,7.25,1);root.add(tag)}}
        zoneTag('GOALKEEPER',-65,-42);zoneTag('DEFENCE',-41,-42);zoneTag('MIDFIELD',0,-42);zoneTag('ATTACK',35,-42);zoneTag('FORWARDS',53,-42);
        pitchRect(-75,75,-48,48);pitchLine([[0,-48],[0,48]]);
        pitchLine(Array.from({{length:81}},(_,i)=>[13*Math.cos(i*Math.PI*2/80),13*Math.sin(i*Math.PI*2/80)]),true);
        pitchLine([[-.8,-.8],[.8,-.8],[.8,.8],[-.8,.8]],true);
        for(const side of [-1,1]){{pitchRect(side*75,side*55,-20,20);pitchRect(side*75,side*65,-9,9);pitchRect(side*76,side*75,-3.7,3.7);pitchLine([[side*63,0],[side*64,.8]],false,.85);
            const postMat=new THREE.MeshStandardMaterial({{color:0xf1f3e9,roughness:.35}});for(const z of [-3.7,3.7]){{const post=new THREE.Mesh(new THREE.CylinderGeometry(.28,.28,7.3,8),postMat);post.position.set(side*76.2,3.6,z);pitch.add(post)}}
            const bar=new THREE.Mesh(new THREE.CylinderGeometry(.22,.22,7.4,8),postMat);bar.rotation.z=Math.PI/2;bar.position.set(side*76.2,7.2,0);pitch.add(bar);
        }}
        const centerMat=new THREE.MeshStandardMaterial({{color:0xc6ef5a,emissive:0x365317,emissiveIntensity:.6,roughness:.3,metalness:.12}});
        function roleZone(role){{const value=String(role||'').toLowerCase();if(value.includes('goalkeeper')||value.includes('goalie'))return -65;if(value.includes('centre-back')||value.includes('center-back')||value.includes('defender')||value.includes('full-back')||value.includes('right-back')||value.includes('left-back'))return -41;if(value.includes('defensive midfield'))return -22;if(value.includes('attacking midfield'))return 21;if(value.includes('midfield'))return 0;if(value.includes('wing')||value.includes('forward')||value.includes('striker')||value.includes('attack'))return 44;return 0}}
        const centerPosition=new THREE.Vector3(roleZone(data.role),3.8,0);
        const center=new THREE.Mesh(new THREE.IcosahedronGeometry(3.3,2),centerMat);center.position.copy(centerPosition);center.userData={{player:data.player,role:data.role,root:true}};root.add(center);meshes.push(center);
        const halo=new THREE.Mesh(new THREE.TorusGeometry(5.8,.16,8,100),new THREE.MeshBasicMaterial({{color:0xc6ef5a,transparent:true,opacity:.9}}));halo.rotation.x=Math.PI/2;halo.position.copy(centerPosition);root.add(halo);
        const maxGoals=Math.max(1,...data.nodes.map(n=>n.joint_goals||0)),maxMinutes=Math.max(1,...data.nodes.map(n=>n.minutes||0)),ppgs=data.nodes.map(n=>n.ppg||0),minPpg=Math.min(0,...ppgs),maxPpg=Math.max(1,...ppgs);
        const byRole=new Map();for(const node of data.nodes){{const zone=roleZone(node.role);if(!byRole.has(zone))byRole.set(zone,[]);byRole.get(zone).push(node)}}
        const nodes=[];for(const [zone,items] of byRole)items.forEach((node,index)=>nodes.push({{...node,zone,lane:index,lanes:items.length}}));
        const spheres=[];
        nodes.forEach((node,i)=>{{const spacing=Math.min(7,82/Math.max(node.lanes,1));const z=(node.lane-(node.lanes-1)/2)*spacing;const x=node.zone+((node.lane%3)-1)*1.2;const vector=new THREE.Vector3(x,2.25,z);
            const hue=.08+.25*((node.ppg||0)-minPpg)/(maxPpg-minPpg||1);const color=new THREE.Color().setHSL(hue,.68,.56);
            const size=2.4+2.3*Math.sqrt((node.joint_goals||0)/maxGoals);const mesh=new THREE.Mesh(new THREE.SphereGeometry(size,16,12),new THREE.MeshStandardMaterial({{color,roughness:.3,metalness:.12,emissive:color,emissiveIntensity:.12}}));
            mesh.position.copy(vector);mesh.userData={{...node,index:i}};root.add(mesh);meshes.push(mesh);spheres.push(mesh);
            const curve=new THREE.QuadraticBezierCurve3(centerPosition.clone(),new THREE.Vector3((centerPosition.x+vector.x)/2,5,(centerPosition.z+vector.z)/2),vector.clone());const geometry=new THREE.BufferGeometry().setFromPoints(curve.getPoints(18));const line=new THREE.Line(geometry,new THREE.LineBasicMaterial({{color,transparent:true,opacity:.55}}));lineGroup.add(line);
        }});
        const tip=document.getElementById('tip');let jointMode=true,topMode=false,hovered=null;
    function showTip(object,x,y){{if(!object){{tip.style.display='none';return}}const d=object.userData;tip.innerHTML=`<b>${{escapeHtml(d.player||'Player')}}</b><br>Listed role: ${{escapeHtml(d.role||'Unspecified')}}<br>Shared goals: ${{d.joint_goals??0}}<br>Points per game: ${{Number(d.ppg||0).toFixed(2)}}<br>Minutes together: ${{Number(d.minutes||0).toLocaleString()}}`;tip.style.display='block';tip.style.left=Math.min(x+14,innerWidth-230)+'px';tip.style.top=Math.max(8,Math.min(y+14,innerHeight-130))+'px'}}
    function escapeHtml(value){{const el=document.createElement('span');el.textContent=value;return el.innerHTML}}
    function pick(event){{const rect=renderer.domElement.getBoundingClientRect();pointer.x=((event.clientX-rect.left)/rect.width)*2-1;pointer.y=-((event.clientY-rect.top)/rect.height)*2+1;raycaster.setFromCamera(pointer,camera);return raycaster.intersectObjects(meshes,false)[0]?.object||null}}
    renderer.domElement.addEventListener('pointermove',event=>{{hovered=pick(event);renderer.domElement.style.cursor=hovered?'pointer':'grab';showTip(hovered,event.clientX,event.clientY)}});
    renderer.domElement.addEventListener('pointerleave',()=>showTip(null));
    renderer.domElement.addEventListener('click',event=>{{const hit=pick(event);if(!hit)return;if(hit.userData.root){{controls.target.set(0,0,0);return}}document.querySelector('.sub').textContent=`${{hit.userData.player}} · ${{hit.userData.joint_goals||0}} shared goal involvements`;spheres.forEach(mesh=>mesh.material.emissiveIntensity=mesh===hit?.45:.08);controls.target.copy(hit.position.clone().multiplyScalar(.23))}});
    document.getElementById('metric').onclick=event=>{{jointMode=!jointMode;event.currentTarget.setAttribute('aria-pressed',String(jointMode));event.currentTarget.textContent=jointMode?'Joint goals':'Minutes';document.querySelector('.legend').textContent=jointMode?'Node size = shared goals · links = teammate connection':'Node size = shared minutes · links = teammate connection';spheres.forEach(mesh=>{{const value=jointMode?(mesh.userData.joint_goals||0):(mesh.userData.minutes||0);const scale=jointMode?Math.sqrt(value/maxGoals):Math.sqrt(value/maxMinutes);mesh.scale.setScalar(.72+.72*scale)}})}};
    document.getElementById('layout').onclick=event=>{{topMode=!topMode;event.currentTarget.setAttribute('aria-pressed',String(topMode));event.currentTarget.textContent=topMode?'3D angle':'Top-down';if(topMode){{camera.up.set(0,0,-1);camera.position.set(0,122,.01)}}else{{camera.up.set(0,1,0);camera.position.set(82,82,92)}}controls.target.set(0,0,0);camera.lookAt(0,0,0);controls.update()}};
    document.getElementById('reset').onclick=()=>{{topMode=false;const view=document.getElementById('layout');view.setAttribute('aria-pressed','false');view.textContent='Top-down';camera.up.set(0,1,0);controls.target.set(0,0,0);camera.position.set(82,82,92);controls.update();spheres.forEach(mesh=>mesh.material.emissiveIntensity=.12);document.querySelector('.sub').textContent=`${{data.nodes.length}} teammates · role-based zones · not a match lineup`}};
    function resize(){{const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/Math.max(1,h);camera.updateProjectionMatrix()}}new ResizeObserver(resize).observe(host);window.addEventListener('resize',resize);resize();
    function animate(){{requestAnimationFrame(animate);controls.update();renderer.render(scene,camera)}}animate();
    </script></body></html>"""


def startup():
    if not DATA_READY:
        return [], [], [], [], None, 0
    with db() as connection:
        seasons = [row[0] for row in connection.execute("SELECT DISTINCT season FROM performance WHERE season IS NOT NULL").fetchall()]
        seasons.sort(key=season_key, reverse=True)
        competitions = connection.execute("SELECT competition_name,COUNT(*) n FROM performance WHERE competition_name IS NOT NULL GROUP BY 1 ORDER BY n DESC,competition_name").fetchall()
        positions = [row[0] for row in connection.execute("SELECT DISTINCT main_position FROM profile WHERE main_position IS NOT NULL ORDER BY 1").fetchall()]
        players = connection.execute("""
            WITH latest AS (SELECT player_id,ARG_MAX(value_eur,value_date) AS latest_value FROM market_values WHERE value_eur>0 GROUP BY player_id)
            SELECT p.player_id,SPLIT_PART(p.player_name,' (',1),p.citizenship FROM latest m JOIN profile p USING(player_id)
            ORDER BY m.latest_value DESC LIMIT 120
        """).fetchall()
        total = sum(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("player_profiles", "player_performances", "player_market_values", "player_injuries", "player_national_performances", "player_teammates", "player_transfers", "team_details", "team_competitions_seasons", "team_children"))
    options = [{"label": f"{name} · {country or 'Unknown'}", "value": str(player_id)} for player_id, name, country in players]
    return seasons, competitions, positions, options, (options[0]["value"] if options else None), total


SEASONS, COMPETITIONS, POSITIONS, PLAYER_OPTIONS, DEFAULT_PLAYER, ROW_COUNT = startup()
DEFAULT_SEASON = next((season for season in SEASONS if season == "23/24"), SEASONS[0] if SEASONS else None)
app = dash.Dash(__name__)
app.title = "Touchline | Player Intelligence"
server = app.server

PANEL = {"backgroundColor": "white", "border": "1px solid #e1e6dc", "borderRadius": "6px", "padding": "15px"}
PAGE = {"fontFamily": "'Trebuchet MS', 'Segoe UI', sans-serif", "backgroundColor": PAPER, "color": INK, "minHeight": "100vh", "paddingBottom": "35px"}

if not DATA_READY:
    app.layout = html.Main(style={**PAGE, "padding": "40px"}, children=[html.H1("Touchline: Player Intelligence"), html.P("Build the local dataset before starting the dashboard."), html.Pre("pip install -r requirements.txt\npython setup_dataset.py\npython app.py")])
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
            # 全局筛选器
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

            # 多标签页导航（并列同级关系，不再嵌套）
            dcc.Tabs(id="dashboard-tabs", value="performance", children=[
                # 1. Performance 标签页
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

                    # 底部全球地图组件（替换原表格）
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
                        overlay_style={"visibility": "visible", "backgroundColor": "rgba(255,255,255,0.62)"},
                    ),
                ]),

                # 2. Player 360 + 3D 标签页
                dcc.Tab(label="Player 360 + 3D", value="player", children=[
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
                    html.Section(style={**PANEL, "marginTop": "12px"}, children=[html.Div(
                        [html.H3("3D teammate network", style={"fontSize": "16px", "margin": "0"}),
                         html.P("Rotate, zoom, inspect teammates, and switch network encodings.",
                                style={"fontSize": "12px", "color": MUTED, "margin": "4px 0 10px"})]),
                                                                                 html.Iframe(id="player-network",
                                                                                             title="Interactive Three.js teammate network",
                                                                                             srcDoc=network_document({
                                                                                                                         "player": "Loading player",
                                                                                                                         "nodes": []}),
                                                                                             sandbox="allow-scripts allow-same-origin",
                                                                                             style={"width": "100%",
                                                                                                    "height": "570px",
                                                                                                    "border": "0",
                                                                                                    "borderRadius": "4px",
                                                                                                    "backgroundColor": "#10251e"})]),
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
                dcc.Tab(label="Injuries & availability", value="injuries", children=[html.Section(
                    style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(180px,1fr))",
                           "gap": "10px", "margin": "12px 0"},
                    children=[kpi("INJURY RECORDS", "injury-count"), kpi("DAYS MISSED", "days-missed"),
                              kpi("GAMES MISSED", "games-missed"), kpi("PLAYERS AFFECTED", "players-injured")]),
                                                                                     html.Section(
                                                                                         style={"display": "grid",
                                                                                                "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                                                                                                "gap": "12px"},
                                                                                         children=[card(
                                                                                             "Days missed by injury type",
                                                                                             "injury-types"), card(
                                                                                             "Availability by year",
                                                                                             "injury-history")]),
                                                                                     html.Section(style={**PANEL,
                                                                                                         "marginTop": "12px"},
                                                                                                  children=[html.H3(
                                                                                                      "Longest absences",
                                                                                                      style={
                                                                                                          "fontSize": "16px"}),
                                                                                                            table(
                                                                                                                "injury-table",
                                                                                                                [(
                                                                                                                 "Player",
                                                                                                                 "player_name",
                                                                                                                 False),
                                                                                                                 (
                                                                                                                 "Injury",
                                                                                                                 "injury_reason",
                                                                                                                 False),
                                                                                                                 (
                                                                                                                 "Season",
                                                                                                                 "season",
                                                                                                                 False),
                                                                                                                 (
                                                                                                                 "Days",
                                                                                                                 "days_missed",
                                                                                                                 True),
                                                                                                                 (
                                                                                                                 "Games",
                                                                                                                 "games_missed",
                                                                                                                 True)])])]),

                # 5. National & clubs 标签页
                dcc.Tab(label="National & clubs", value="teams", children=[html.Section(
                    style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(min(100%,340px),1fr))",
                           "gap": "12px", "marginTop": "12px"},
                    children=[card("International appearances", "national-caps"),
                              card("International goals and caps", "national-goals")]), html.Section(
                    style={**PANEL, "marginTop": "12px"},
                    children=[html.H3("Club season comparison", style={"fontSize": "16px"}),
                              card("Points by club", "club-points"), table("club-table", [("Rank", "rank", True),
                                                                                          ("Club", "team_name", False),
                                                                                          ("Competition",
                                                                                           "competition_name", False),
                                                                                          ("Season", "season", False),
                                                                                          ("Played", "played", True),
                                                                                          ("Wins", "wins", True),
                                                                                          ("Draws", "draws", True),
                                                                                          ("Losses", "losses", True),
                                                                                          ("GF", "goals_for", True),
                                                                                          ("GA", "goals_against", True),
                                                                                          ("Points", "points", True)],
                                                                           15)])]),
            ]),
            html.Footer(["Source: ",
                         html.A("salimt/football-datasets", href="https://github.com/salimt/football-datasets",
                                target="_blank", style={"color": INK}),
                         " · Transfermarkt-derived data. Verify source usage rights before redistribution."],
                        style={"fontSize": "11px", "color": MUTED, "padding": "16px 2px"}),
        ]),
    ])

    # ==================== 后端回调函数区域 ====================

    if DATA_READY:
        # 1. 上方三张图表与顶部 KPI 的回调
        @app.callback(
            [
                Output("metric-leaderboard", "figure"),
                Output("goal-assist-scatter", "figure"),
                Output("season-trend", "figure"),
                Output("metric-leader", "children"),
                Output("players-count", "children"),
                Output("goals-total", "children"),
                Output("assists-total", "children"),
                Output("minutes-total", "children"),
            ],
            [
                Input("season-filter", "value"),
                Input("competition-filter", "value"),
                Input("position-filter", "value"),
                Input("player-search", "value"),
                Input("metric-filter", "value"),
            ],
        )
        def update_performance_tab(season, competition, position, player_ids, metric_key):
            df = player_aggregates(season, competition, position, player_ids)
            if df.empty:
                empty = empty_figure("No performance records match the selected filters.")
                return empty, empty, empty, "No data available", "0", "0", "0", "0"

            metric_label = METRICS.get(metric_key, "Goals")
            total_players = len(df)
            total_goals = number(df["goals"].sum())
            total_assists = number(df["assists"].sum())
            total_minutes = number(df["minutes_played"].sum())

            # 排行榜
            top_df = df.sort_values(by=metric_key, ascending=False).head(15).iloc[::-1]
            if top_df.empty or top_df[metric_key].sum() == 0:
                leader_fig = empty_figure(f"No {metric_label.lower()} recorded for this selection.")
            else:
                leader_fig = px.bar(
                    top_df, x=metric_key, y="player_name", orientation="h",
                    text=metric_key, color=metric_key,
                    color_continuous_scale=["#b5d656", GREEN, INK]
                )
                leader_fig.update_coloraxes(showscale=False)
                leader_fig.update_traces(texttemplate="%{text:,}", textposition="outside")
                leader_fig.update_layout(xaxis_title=metric_label, yaxis_title="")
                leader_fig = finish(leader_fig)

            top_row = df.sort_values(by=metric_key, ascending=False).iloc[0] if not df.empty else None
            leader_text = f"Top {metric_label.lower()}: {top_row['player_name']} · {numeric_or_zero(top_row[metric_key]):,}" if top_row is not None else ""

            # 散点图
            scatter_df = df.copy()
            if scatter_df.empty:
                scatter_fig = empty_figure("No scatter data available.")
            else:
                scatter_fig = px.scatter(
                    scatter_df, x="goals", y="assists", size="minutes_played",
                    color="main_position", hover_name="player_name",
                    color_discrete_map={"Attack": GREEN, "Midfield": ORANGE, "Defender": BLUE},
                    size_max=28
                )
                scatter_fig.update_layout(xaxis_title="Goals", yaxis_title="Assists")
                scatter_fig = finish(scatter_fig)

            # 跨赛季趋势图
            where, params = where_for(None, competition, position, player_ids)
            trend_df = frame(f"""
                SELECT f.season, SUM(COALESCE(f.goals,0)) AS goals, SUM(COALESCE(f.assists,0)) AS assists
                FROM performance f JOIN profile p USING(player_id) WHERE f.season IS NOT NULL {where}
                GROUP BY f.season ORDER BY f.season
            """, params)

            if trend_df.empty:
                trend_fig = empty_figure("No historical trend data available.")
            else:
                trend_fig = go.Figure()
                trend_fig.add_trace(
                    go.Scatter(x=trend_df["season"], y=trend_df["goals"], mode="lines+markers", name="Goals",
                               line=dict(color=GREEN, width=3)))
                trend_fig.add_trace(
                    go.Scatter(x=trend_df["season"], y=trend_df["assists"], mode="lines+markers", name="Assists",
                               line=dict(color=ORANGE, width=3)))
                trend_fig.update_layout(xaxis_title="Season", yaxis_title="Total Count")
                trend_fig = finish(trend_fig)

            return leader_fig, scatter_fig, trend_fig, leader_text, number(
                total_players), total_goals, total_assists, total_minutes


        # 2. 底部全球地图的回调
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

    if __name__ == "__main__":
        app.run(debug=False)