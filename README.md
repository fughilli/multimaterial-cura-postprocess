# Multimaterial Print Postprocessor for Cura

This script postprocesses generated gcode files for multimaterial prints to
enforce correct temperature ramping and nozzle wiping for "oozy" materials. For
more information, see the related [feature
request](https://github.com/Ultimaker/Cura/issues/5826).

## Running

You will need [Bazel](https://bazel.build/).

To run this script, checkout the repository, and then execute:

```
bazel run :postprocess -- $(pwd)/your_gcode_file.gcode
```

This will generate an output file named `your_gcode_file.postprocessed.gcode` in
the same directory with the modifications.
