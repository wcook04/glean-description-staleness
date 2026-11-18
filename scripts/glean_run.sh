#!/bin/bash
# Runs inside the Glean image. /work is this repository.
set -x
cd /work
G="glean --db-root /tmp/db --schema dir:/glean-demo/schema/source"
mkdir -p out
{
glean --help
glean create --help
glean write --help
glean complete --help
glean derive --help
glean query --help
} > out/help.txt 2>&1

$G create --db ex/base
$G write --db ex/base facts/base/*.json
$G complete --db ex/base
$G finish --db ex/base
$G list
$G query --db ex/base 'python.CalleeToCaller _' > out/base_callee_to_caller.json
$G query --db ex/base 'python.DefinitionLocation _' > out/base_definition_location.json
$G create --db ex/variant --incremental ex/base --exclude utils.py
$G write --db ex/variant facts/variant/*.json
$G complete --db ex/variant
$G finish --db ex/variant
$G list
$G query --db ex/variant 'python.CalleeToCaller _' > out/variant_callee_to_caller.json
$G query --db ex/variant 'python.DefinitionLocation _' > out/variant_definition_location.json
$G query --db ex/variant 'src.File _' > out/variant_files.json
wc -c out/*
