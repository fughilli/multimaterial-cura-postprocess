py_library(
    name = "postprocess_lib",
    srcs = ["postprocess_lib.py"],
)

py_binary(
    name = "postprocess",
    srcs = ["postprocess.py"],
    deps = [":postprocess_lib"],
)

py_test(
    name = "postprocess_lib_test",
    srcs = ["postprocess_lib_test.py"],
    deps = [":postprocess_lib"],
)
