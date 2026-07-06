# Example course for the GINI Teaching Center reference server

    COURSE_ROOT=./example PORT=8080 python ../server.py

Then point a GINI client at http://localhost:8080 (course `cs4480-fall26`). The client validates
each lesson pack against `pack_hash` in the manifest.
