# Multimaterial Print Postprocessor for Cura

This script postprocesses generated gcode files for multimaterial prints to
enforce correct temperature ramping and nozzle wiping for "oozy" materials. For
more information, see the related [feature
request](https://github.com/Ultimaker/Cura/issues/5826).

## Cura Set-up

You will need to add two Gcode comments to your tool switch scripts in Cura so
that the postprocessing script knows where the tool changes occur.

Navigate to `Settings -> Printer -> Manage printers...`. Select your printer
from the dialog, and press `Machine Settings`. For each extruder, replace
"Extruder Start G-code" with:
```
; EXTRUDER START HOME
G0 X{prime_tower_position_x} Y{prime_tower_position_y}
```
and replace "Extruder End G-code" with:
```
; EXTRUDER END HOME
G0 X{prime_tower_position_x} Y{prime_tower_position_y}
```

## Running

You will need [Bazel](https://bazel.build/).

To run this script, checkout the repository, and then execute:

```
bazel run :postprocess -- $(pwd)/your_gcode_file.gcode
```

This will generate an output file named `your_gcode_file.postprocessed.gcode` in
the same directory with the modifications.
