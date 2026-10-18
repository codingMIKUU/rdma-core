#!/bin/bash
set -e

usage() {
    cat <<EOF
Usage: $0 [xrc|rc]

  xrc  Build rdma-core with the CHORD XRC pool enabled.
  rc   Build rdma-core with the CHORD XRC pool disabled.

With no argument, the existing CMake cache/default is preserved.  The mode
can also be selected with CHORD_XRC_POOL=ON or CHORD_XRC_POOL=OFF.
EOF
}

CHORD_MODE="${CHORD_XRC_POOL:-}"
case "${1:-}" in
    xrc|XRC|on|ON)
        CHORD_MODE=ON
        shift
        ;;
    rc|RC|off|OFF)
        CHORD_MODE=OFF
        shift
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    "")
        ;;
    *)
        echo "Unknown build mode: $1" >&2
        usage >&2
        exit 2
        ;;
esac

CHORD_CMAKE_FLAG=""
if [ -n "$CHORD_MODE" ]; then
    CHORD_CMAKE_FLAG="-DCHORD_XRC_POOL=$CHORD_MODE"
    echo "CHORD XRC pool build mode: $CHORD_MODE"
fi

SRCDIR=`dirname $0`
BUILDDIR="$SRCDIR/build"

mkdir -p "$BUILDDIR"

if hash cmake3 2>/dev/null; then
    # CentOS users are encouraged to install cmake3 from EPEL
    CMAKE=cmake3
else
    CMAKE=cmake
fi

if hash ninja-build 2>/dev/null; then
    # Fedora uses this name
    NINJA=ninja-build
elif hash ninja 2>/dev/null; then
    NINJA=ninja
fi

cd "$BUILDDIR"

if [ "x$NINJA" == "x" ]; then
    echo "Ninja not found, falling back to make"
    $CMAKE -DCMAKE_BUILD_TYPE=Debug -DIN_PLACE=1 ${EXTRA_CMAKE_FLAGS:-} $CHORD_CMAKE_FLAG ..
    make
else
    echo "Ninja found, using it"
    $CMAKE -DIN_PLACE=1 -GNinja ${EXTRA_CMAKE_FLAGS:-} $CHORD_CMAKE_FLAG ..
    $NINJA
fi
