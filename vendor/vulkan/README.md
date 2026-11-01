# Vulkan loader

`vulkan-1.dll` is the x64 Vulkan loader from LunarG's Vulkan Runtime
1.3.296.0 components archive. It is included so builds do not depend on
runtime downloads being available to CI runners. The loader does not replace
the Vulkan graphics driver required for GPU processing.

- Original archive: https://sdk.lunarg.com/sdk/download/1.3.296.0/windows/vulkan-runtime-components.zip
- Archive SHA-256: `e0429f91cf356fe99e381f7d22e568ac81945669f1be1772d933a0d189230452`
- DLL SHA-256: `8644669401d2c080ea10cfea2ba8a7c63d76e10a4e7ae8246837df1dfd157ea8`
- Source: https://github.com/KhronosGroup/Vulkan-Loader/tree/v1.3.296
- License: [vulkan-loader-LICENSE.txt](../vulkan-loader-LICENSE.txt)
