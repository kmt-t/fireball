#!/bin/bash
# validation_script.sh
# Cross-sectional keyword validation for Fireball components

echo "Starting cross-sectional keyword validation..."

# 1. Check for forbidden containers
forbidden_containers=("std::list" "std::vector")
for container in "${forbidden_containers[@]}"; do
    if grep -r "$container" src/ inc/ | grep -v "test"; then
        echo "ERROR: Forbidden container '$container' detected!"
        exit 1
    fi
done

# 2. Check for keywords in new/modified code (simplified heuristic)
# In a real environment, this would parse file-level tags.
# For now, we ensure new headers contain the required {Keyword} tags found in docs.
required_tags=("{3TierSeparation}" "{CooperativeMultitasking}" "{CSPCommunication}")
for tag in "${required_tags[@]}"; do
    if ! grep -r "$tag" src/ inc/ --include=\*.hxx --include=\*.cpp | grep -v "docs/"; then
        # This is a heuristic: some tags might not be used everywhere.
        # But this alerts us to missing architectural markers.
        echo "WARNING: Missing architectural marker '$tag' in implementation."
    fi
done

echo "Validation completed."
