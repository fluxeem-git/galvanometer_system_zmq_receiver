cmake_minimum_required(VERSION 3.21)

option(
  GALVANOMETER_USE_SYSTEM_DEPENDENCIES
  "Use cppzmq and nlohmann-json supplied by vcpkg or the operating system"
  OFF
)

# Visual Studio can inject a machine-wide vcpkg integration even when this
# project did not request it. Disable that integration for the self-contained
# build so customer results do not depend on the developer machine.
if(
  CMAKE_GENERATOR MATCHES "Visual Studio"
  AND NOT GALVANOMETER_USE_SYSTEM_DEPENDENCIES
)
  set(CMAKE_VS_GLOBALS "VcpkgEnabled=false")
endif()

project(
  galvanometer_zmq_receiver
  VERSION 1.0.0
  DESCRIPTION "Customer ZeroMQ receiver example for Terminal RotationStateV1"
  LANGUAGES CXX
)

if(GALVANOMETER_USE_SYSTEM_DEPENDENCIES)
  find_package(cppzmq CONFIG REQUIRED)
  find_package(nlohmann_json CONFIG REQUIRED)
else()
  # libzmq 4.3.5 predates CMake 4, which requires an explicit compatibility
  # floor for subprojects whose own cmake_minimum_required is older than 3.5.
  set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
  set(BUILD_SHARED OFF CACHE BOOL "" FORCE)
  set(BUILD_STATIC ON CACHE BOOL "" FORCE)
  set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
  set(WITH_DOCS OFF CACHE BOOL "" FORCE)
  set(WITH_LIBSODIUM OFF CACHE BOOL "" FORCE)
  set(ENABLE_CURVE OFF CACHE BOOL "" FORCE)
  set(ENABLE_DRAFTS OFF CACHE BOOL "" FORCE)
  set(ENABLE_WS OFF CACHE BOOL "" FORCE)
  set(ENABLE_CPACK OFF CACHE BOOL "" FORCE)
  add_subdirectory(third_party/libzmq third_party/libzmq-build EXCLUDE_FROM_ALL)

  add_library(cppzmq INTERFACE)
  target_include_directories(
    cppzmq
    INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/cppzmq
  )
  target_link_libraries(cppzmq INTERFACE libzmq-static)

  add_library(nlohmann_json INTERFACE)
  add_library(nlohmann_json::nlohmann_json ALIAS nlohmann_json)
  target_include_directories(
    nlohmann_json
    INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/third_party/nlohmann_json/single_include
  )
endif()

add_executable(rotation_receiver cpp/src/main.cpp)
target_compile_features(rotation_receiver PRIVATE cxx_std_17)
target_link_libraries(
  rotation_receiver
  PRIVATE cppzmq nlohmann_json::nlohmann_json
)

if(MSVC)
  target_compile_options(rotation_receiver PRIVATE /W4 /permissive- /utf-8)
else()
  target_compile_options(rotation_receiver PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
  add_test(NAME rotation_receiver_help COMMAND rotation_receiver --help)
endif()
