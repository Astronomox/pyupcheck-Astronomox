---
name: Changelog miss
about: pyupcheck missed a breaking change for a package
title: "[MISS] <package> <from_version> -> <to_version>"
labels: changelog-miss
assignees: Astronomox
---

## Package

Name:
From version:
To version:

## What pyupcheck reported

```
paste output of: pyupcheck diff <package> <from> <to>
```

## What was actually breaking

<!-- Describe what changed in this version that pyupcheck didn't catch -->

## Link to changelog or release notes

<!-- URL to the package's official changelog for this release -->

## Your code that was affected

```python
# paste the usage that was affected
```

---

Changelog misses are the most impactful thing to fix. Thank you for reporting.
