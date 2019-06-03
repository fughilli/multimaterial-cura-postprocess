import unittest
import re

import postprocess_lib

TEMP_SNIPPET = """
T0
M104 S220
T1
M109 S110
M104 T1 S215
M109 T0 S100
T0
M109 S0
T1
M109 S0
"""

TEMP_T0_IDLE = 100
TEMP_T1_IDLE = 110
TEMP_T0_PRINTING = 220
TEMP_T1_PRINTING = 215

IDLE_TEMPS = {0: TEMP_T0_IDLE, 1: TEMP_T1_IDLE}
PRINTING_TEMPS = {0: TEMP_T0_PRINTING, 1: TEMP_T1_PRINTING}


class TestPostprocess(unittest.TestCase):

    def assertGcodeEqual(self, gcode_a, gcode_b):
        self.assertEqual(postprocess_lib.parse_gcode(gcode_a),
                         postprocess_lib.parse_gcode(gcode_b))

    def assertGcodeLinesEqual(self, gcode_lines_a, gcode_lines_b):
        self.assertEqual(len(gcode_lines_a), len(gcode_lines_b))
        for gcode_a, gcode_b in zip(gcode_lines_a, gcode_lines_b):
            self.assertEqual(gcode_a, gcode_b)

    def test_gcode_equal(self):
        self.assertGcodeEqual('G0 X100 Y200', 'G0 Y200 X100')

    def test_parse_gcode_simple(self):
        self.assertEqual(postprocess_lib.parse_gcode('G0'), ('G0', {}))

    def test_parse_gcode_comment(self):
        self.assertEqual(postprocess_lib.parse_gcode('; foobar foo'),
                         (None, {}))
        self.assertEqual(
            postprocess_lib.parse_gcode('G0 ; this is a line comment'),
            ('G0', {}))

    def test_make_gcode_simple(self):
        self.assertEqual(postprocess_lib.make_gcode('G0', {}), 'G0')

    def test_make_gcode_arguments(self):
        self.assertIn(postprocess_lib.make_gcode('G0', {
            'X': 10,
            'Y': 3.5
        }), ['G0 X10 Y3.5', 'G0 Y3.5 X10'])

    def test_parse_gcode_arguments(self):
        self.assertEqual(postprocess_lib.parse_gcode('G0 X1.15 F2000'),
                         ('G0', {
                             'X': 1.15,
                             'F': 2000
                         }))

    def test_rewrite_non_matching(self):
        self.assertGcodeEqual(
            postprocess_lib.rewrite('M104 T1 S200',
                                    op_regex=re.compile('G[01]'),
                                    override={'F': 1000},
                                    delete=['S']), 'M104 T1 S200')
        self.assertGcodeEqual(postprocess_lib.rewrite_move('G82', 1000, None),
                              'G82')

    def test_rewrite_matching(self):
        self.assertGcodeEqual(
            postprocess_lib.rewrite('M104 T1 S200',
                                    op_regex=re.compile('M10[49]'),
                                    override={'S': 170}), 'M104 T1 S170')

    def test_rewrite_move_feedrate(self):
        self.assertGcodeEqual(
            postprocess_lib.rewrite_move('G1 F6400 X100 Y200', 400, None),
            'G1 F400 X100 Y200')

    def test_processor_init(self):
        processor = postprocess_lib.Processor()
        self.assertEquals(processor.active_tool, 0)
        self.assertEquals(processor.current_z, 0)

    def test_processor_z(self):
        processor = postprocess_lib.Processor()
        self.assertEquals(processor.current_z, 0)
        processor.process_line('G0 Z0.5')
        self.assertEquals(processor.current_z, 0.5)
        processor.process_line('G0 Z0.9')
        self.assertEquals(processor.current_z, 0.9)

    def test_processor_tool(self):
        processor = postprocess_lib.Processor()
        self.assertEquals(processor.active_tool, 0)
        processor.process_line('T1')
        self.assertEquals(processor.active_tool, 1)
        processor.process_line('M104 T2 S100')
        self.assertEquals(processor.active_tool, 1)
        processor.process_line('T0')
        self.assertEquals(processor.active_tool, 0)

    def test_temp_processor_init(self):
        temp_processor = postprocess_lib.TempProcessor()
        self.assertEquals(temp_processor.idle_temps, {})
        self.assertEquals(temp_processor.printing_temps, {})

    def test_temp_processor_extraction(self):
        temp_processor = postprocess_lib.TempProcessor()
        temp_processor.process_lines(TEMP_SNIPPET.split('\n'))
        self.assertEquals(temp_processor.idle_temps, IDLE_TEMPS)
        self.assertEquals(temp_processor.printing_temps, PRINTING_TEMPS)

    def test_block_temp_annotation(self):
        block = postprocess_lib.Block(lines=[
            "G0 X1 Y1", "G0 X20 Y1", "G1 X2 Y2 E5", "G0 X3 Y10", "G1 X3 Y5 E10"
        ],
                                      state=postprocess_lib.S_PRIME_BLOCK,
                                      start_z=0,
                                      finish_z=0,
                                      active_tool=0)
        block.add_temperatures(True, IDLE_TEMPS, PRINTING_TEMPS, {}, {})
        self.assertGcodeLinesEqual(block.lines, [
            "G0 X1 Y1", "G0 X20 Y1", "M109 T0 S220", "G1 X2 Y2 E5", "G0 X3 Y10",
            "G1 X3 Y5 E10"
        ])
        self.assertEqual(block.finish_target_temps[0], 220)
        self.assertEqual(block.finish_reached_target[0], True)

    def test_block_temp_annotation_no_wait(self):
        block = postprocess_lib.Block(lines=[
            "G0 X1 Y1", "G0 X20 Y1", "G1 X2 Y2 E5", "G0 X3 Y10", "G1 X3 Y5 E10"
        ],
                                      state=postprocess_lib.S_PRIME_BLOCK,
                                      start_z=0,
                                      finish_z=0,
                                      active_tool=0)
        block.add_temperatures(False, IDLE_TEMPS, PRINTING_TEMPS, {}, {})
        self.assertGcodeLinesEqual(block.lines, [
            "G0 X1 Y1", "G0 X20 Y1", "M104 T0 S220", "G1 X2 Y2 E5", "G0 X3 Y10",
            "G1 X3 Y5 E10"
        ])
        self.assertEqual(block.finish_target_temps[0], 220)
        self.assertEqual(block.finish_reached_target[0], False)

    def test_block_temp_annotation_already_hot(self):
        block = postprocess_lib.Block(lines=[
            "G0 X1 Y1", "G0 X20 Y1", "G1 X2 Y2 E5", "G0 X3 Y10", "G1 X3 Y5 E10"
        ],
                                      state=postprocess_lib.S_PRIME_BLOCK,
                                      start_z=0,
                                      finish_z=0,
                                      active_tool=0)
        block.add_temperatures(True, IDLE_TEMPS, PRINTING_TEMPS,
                               {0: TEMP_T0_PRINTING}, {0: True})
        self.assertGcodeLinesEqual(block.lines, [
            "G0 X1 Y1", "G0 X20 Y1", "G1 X2 Y2 E5", "G0 X3 Y10", "G1 X3 Y5 E10"
        ])
        self.assertEqual(block.finish_target_temps[0], 220)
        self.assertEqual(block.finish_reached_target[0], True)

    def test_temp_minimize(self):
        temp_minimize_processor = postprocess_lib.TempMinimizeProcessor()
        lines = [
            "T0",
            "M104 S100",
            "M104 T1 S200",
            "T1",
            "M109 S200",
            "T0",
            "M109 S100",
            "T1",
            "M104 S200",
            "M109 T0 S100",
        ]
        fixed_lines = [
            "T0",
            "M104 S100",
            "M104 T1 S200",
            "T1",
            "M109 S200",
            "T0",
            "M109 S100",
            "T1",
        ]
        temp_minimize_processor.process_lines(lines)
        self.assertGcodeLinesEqual(temp_minimize_processor.get_lines(),
                                   fixed_lines)


if __name__ == '__main__':
    unittest.main()
