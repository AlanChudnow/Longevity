# Longevity Risk Calculator — Claude Code Instructions

## Architecture rules
- ALL survival math stays in src/health_models.py — never in gui.py
- ALL Apple Health parsing stays in src/apple_health.py
- ALL lab PDF extraction stays in src/lab_parser.py
- gui.py only calls functions from the above modules, never implements logic

## Key paths
- Apple Health: C:\Users\Daddy\Downloads\export.xml
- Design mockup: design\Longevity_Risk_Calculator_v2.html
- Life table cache: data\life_table.parquet

## Model layers (in order, all multiplicative)
1. CDC life table baseline (mx)
2. ASCVD Pooled Cohort Equations → relative hazard
3. VO2 Max percentile → hazard modifier
4. HRV + resting HR → autonomic modifier
5. Grip strength / dead hang → muscle modifier

## Never do
- Never use the toy predict_relative_hazard() from the original prototype
- Never hardcode mortality rates — always use life table
- Never block the GUI thread during API calls (use threading)

## GUI framework
- CustomTkinter (not standard Tkinter)
- Match design/Longevity_Risk_Calculator_v2.html for layout and color
- Single window, inputs top, results bottom

## Testing
- After any change to health_models.py, run: python -m pytest tests/
- Keep a test fixture with known ASCVD inputs and expected outputs
