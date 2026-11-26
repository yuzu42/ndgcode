# ndgcode configuration example (same format as nextdraw_conf.py).
# Copy to ndgcode_conf_local.py (git-ignored), adjust, and load it with
#     nd.load_config("ndgcode_conf_local.py")
# or  python -m ndgcode shell --config ndgcode_conf_local.py
#
# Option names (speed_pendown, pen_pos_up, ...) set nd.options; everything
# else sets nd.params.

# --- NextDraw options ---------------------------------------------------
speed_pendown = 25        # % of max_feed_mm_min (capped by feed_cap_mm_min)
speed_penup = 75
accel = 75                # % of max_accel_mm_s2, written to $120/$121 if it changes
pen_pos_down = 0          # % height; 0 = S1000 = the pen-insertion position
pen_pos_up = 45           # % height; 45 = S550. Run "calibrate pen" to find yours.
pen_rate_lower = 50       # < 75: lowered in steps (gentler touch-down)
pen_rate_raise = 75

# --- machine (VigoTec VG-X4, GRBL "VigoWriter 1.1f") -----------------------
travel_x_mm = 305.0       # PROVISIONAL (vendor range 310 minus pull-off)
travel_y_mm = 251.0       # PROVISIONAL (vendor range 256 minus pull-off)
flip_y = True             # GRBL +Y points away from the operator
pull_off_mm = 5.0         # after pushing the carriage into the Home corner
max_feed_mm_min = 5000.0  # $110/$111
feed_cap_mm_min = 1000.0  # PROVISIONAL safety cap
max_accel_mm_s2 = 400.0   # accel = 100 %
servo_settle_ms = 150     # pause after every pen move (minimum measured: 100)
s_full_down = 1000        # servo S at 0 % height
s_full_up = 0             # servo S at 100 % height
s_contact = 800           # the tip leaves the paper here (mid-bed)
# pen_height_map = "bed_map.json"   # from "calibrate bed" in the shell
park_on_disconnect = True
sleep_on_disconnect = True
# port = "/dev/cu.usbserial-120"   # default: auto-detect the CH340
