# ESP-IDF project boilerplate

Each sketch directory is a standard ESP-IDF project:

```
sniffer/
├── CMakeLists.txt          # top-level project file (below)
└── main/
    ├── CMakeLists.txt       # component file (below)
    └── sniffer.c            # the sketch source
```

**Top-level `CMakeLists.txt`:**

```cmake
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(gini32_sniffer)
```

**`main/CMakeLists.txt`:**

```cmake
idf_component_register(SRCS "sniffer.c" INCLUDE_DIRS ".")
```

Swap the source filename (`beacon.c`, `csi.c`) and the `project()` name to build the
others. All three depend only on the `esp_wifi`, `nvs_flash`, and `esp_netif`
components, which ship with ESP-IDF.
