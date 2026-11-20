#include <AppCore/Platform.h>
#include <Ultralight/Ultralight.h>
#include <cstdint>
#include <cstdio>
#include <poll.h>
#include <unistd.h>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    using namespace ultralight;
    constexpr unsigned width = 1920;
    constexpr unsigned height = 1080;

    auto& platform = Platform::instance();
    platform.set_config(Config{});
    platform.set_font_loader(GetPlatformFontLoader());
    platform.set_file_system(GetPlatformFileSystem("/opt/ultralight"));
    platform.set_logger(GetDefaultLogger("/tmp/ultralight.log"));
    auto renderer = Renderer::Create();
    ViewConfig config;
    config.is_accelerated = false;
    config.initial_device_scale = 1.0;
    auto view = renderer->CreateView(width, height, config, nullptr);
    view->LoadURL(argv[1]);

    pollfd input{STDIN_FILENO, POLLIN, 0};
    while (poll(&input, 1, 16) >= 0) {
        if (input.revents & (POLLERR | POLLHUP)) return 0;
        renderer->Update();
        renderer->RefreshDisplay(0);
        renderer->Render();

        if (!(input.revents & POLLIN)) continue;
        char command[32];
        if (read(STDIN_FILENO, command, sizeof command) <= 0) return 0;
        auto* surface = view->surface();
        const uint32_t length = width * height * 4;
        std::fwrite(&length, sizeof length, 1, stdout);
        const auto* pixels = static_cast<const uint8_t*>(surface->LockPixels());
        for (unsigned row = 0; row < height; ++row)
            std::fwrite(pixels + row * surface->row_bytes(), width * 4, 1, stdout);
        surface->UnlockPixels();
        surface->ClearDirtyBounds();
        std::fflush(stdout);
    }
}
