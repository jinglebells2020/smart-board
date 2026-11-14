# Lessons

## Lesson 1: LED with resistor
- Circuit: 5 V → 220 ohm → red LED → ground
- Expected current: (5 - 2) / 220 = 0.0136 A = 13.6 mA
- Accept range: 10 to 17 mA
- Mistakes to catch:
  - LED backwards: current about 0 mA → hint about polarity
  - No resistor: current hits the limit → cut power, explain why
  - Open circuit: 0 mA and 0 V at LED node → hint to check rows

## Lesson 2: Voltage divider
- Circuit: 5 V → 10k → middle node → 10k → ground
- Expected middle voltage: 5 x 10 / (10 + 10) = 2.5 V
- Expected current: 5 / 20000 = 0.00025 A = 0.25 mA
- Accept range: 2.3 to 2.7 V
- Mistakes to catch:
  - Bottom resistor is 1k: 5 x 1 / (10 + 1) = 0.45 V
    → hint about ratio
  - Middle node not connected: reading floats → hint to check
    the probe row

## Lesson 3: ___
- Circuit: ___
- Expected values with calculation: ___
- Mistakes to catch: ___
