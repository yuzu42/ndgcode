"""Default option and parameter values.

``OPTIONS`` mirrors the NextDraw ``options`` namespace (same names, same
meaning), with defaults adapted where the VG-X4 needs it (pen heights).
``PARAMS`` holds NextDraw's secondary parameters plus the machine
parameters for the GRBL plotter. Both can be overridden from a config file
with :meth:`ndgcode.NextDraw.load_config` (same format as ``nextdraw_conf.py``:
plain Python assignments).

Values marked PROVISIONAL are not measured yet; see SPEC.md section 8.
"""

from __future__ import annotations

OPTIONS = dict(
    # --- general (plot + interactive) -------------------------------------
    handling=1,             # 1 technical, 2 handwriting, 3 sketching, 4 constant speed
    speed_pendown=25,       # % of max_feed_mm_min
    speed_penup=75,         # % of max_feed_mm_min
    accel=75,               # % of max_accel_mm_s2
    pen_pos_down=0,         # % height; 0 -> S1000 (pen is inserted at this position)
    pen_pos_up=45,          # % height; 45 -> S550 (S700 dragged on the low part of the bed)
    pen_rate_lower=50,      # 1-100; < 75 emulates a slower servo in S steps
    pen_rate_raise=75,
    model=8,                # accepted for compatibility, no effect
    penlift=1,              # accepted for compatibility, no effect
    homing=True,            # run the configured homing strategy on connect
    port=None,              # serial path, None = auto-detect, "file:out.gcode" = export
    port_config=0,
    # --- interactive -----------------------------------------------------
    units=0,                # 0 in, 1 cm, 2 mm (NextDraw default: inches)
    # --- plot --------------------------------------------------------------
    mode="plot",
    utility_cmd="read_name",
    dist=1.0,
    layer=1,
    copies=1,
    page_delay=15,
    auto_rotate=True,
    preview=False,
    rendering=True,
    reordering=0,
    random_start=False,
    hiding=False,
    report_time=False,
    digest=0,
    webhook=False,
    webhook_url=None,
    offset_start=False,
)

PARAMS = dict(
    # --- NextDraw secondary parameters that keep their meaning -----------
    pen_delay_up=0,         # extra ms after raising the pen
    pen_delay_down=0,       # extra ms after lowering the pen
    auto_clip_lift=True,    # lift the pen where a single XY move is clipped
    clip_to_page=True,      # (plot) passed to the NextDraw digest
    min_gap=0.006,          # (plot) path joining threshold, inches
    report_lifts=False,
    bounds_tolerance=0.003,  # inches, as in NextDraw
    preview_paths=3,
    preview_color_up="LightPink",
    preview_color_down="Blue",
    progress=False,
    pause_warning=True,
    overrides={},           # passed through to the NextDraw digest
    # --- machine: GRBL / VG-X4 -----------------------------------------------
    baud=115200,
    port_match=("usbserial", "wchusbserial", "CH340", "CH341"),  # auto-detect filter
    port_vid=0x1A86,        # WCH (CH340/CH341) USB vendor id
    banner_timeout_s=5.0,
    max_feed_mm_min=5000.0,  # = $110/$111
    feed_cap_mm_min=1000.0,  # PROVISIONAL hard cap until the motion fault is solved
    max_accel_mm_s2=400.0,   # accel=100 % ($120/$121)
    write_accel=True,        # write $120/$121 on connect/update only if changed
    junction_deviation_mm=0.1,  # $11, used by the time estimate
    travel_x_mm=305.0,       # PROVISIONAL: vendor range 310 minus pull-off
    travel_y_mm=251.0,       # PROVISIONAL: vendor range 256 minus pull-off
    flip_x=False,
    flip_y=True,             # measured: GRBL +Y points away from the operator
    swap_xy=False,
    homing_strategy="manual",  # "manual" | "switches"
    pull_off_mm=5.0,         # off both stops after manual homing
    pull_off_feed_mm_min=500.0,
    switch_home_offset_mm=(0.0, 0.0),  # "switches": offset from the switch corner
    park_on_disconnect=True,  # return to the Home corner on disconnect
    sleep_on_disconnect=True,  # $SLP after parking (motors off)
    jog_feed_mm_min=1000.0,
    # pen servo (spindle PWM). S = s_full_down + (s_full_up - s_full_down) * h / 100
    s_full_down=1000,
    s_full_up=0,
    s_contact=800,           # tip leaves the paper here (mid-bed); warn below this lift
    servo_settle_ms=150,     # measured minimum ~100 ms
    servo_sweep_ms=300,      # time for a full S0<->S1000 sweep (pen_rate emulation)
    pen_rate_jump=75,        # rates >= this jump straight to the target
    pen_height_map=None,     # dict or JSON path, see geometry.HeightMap
    # streaming
    stream_mode="count",     # "count" (character counting) | "simple" (one line per ok)
    rx_buffer_bytes=127,     # GRBL RX buffer is 128 bytes
    status_poll_s=0.2,
    simplify_mm=0.01,        # RDP tolerance for draw_path vertices; 0 disables
    decimals=3,
)

#: handling presets: (feed scale, accel scale). Approximations, see SPEC 4.3.
HANDLING = {
    1: (1.0, 1.0),    # technical drawing
    2: (0.8, 1.25),   # handwriting: lower top speed, high acceleration
    3: (1.2, 0.8),    # sketching: higher top speed, lower acceleration
    4: (0.6, 2.0),    # constant speed: GRBL cannot disable acceleration; use max accel
}
