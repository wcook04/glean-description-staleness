# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Utility functions for branch v1."""


# Modified for this test (not part of Glean): utils re-exports what main.py
# uses from lib, so main.py imports nothing from lib directly.
from lib import calculate_result, helper_function, HelperClass  # noqa: F401


def format_output(data):
    """Format output for v1."""
    return f"v1_result: {data}"
