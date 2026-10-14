"""Option names shared by the UI and the players (no libmpv needed to import)."""

# Decoder modes
AUTO, GPU_COPY, GPU_ZERO_COPY, CPU = "auto", "gpu-copy", "gpu-zero-copy", "cpu"
DECODER_MODES = {
    AUTO: "Auto — CPU for small videos, GPU for large ones",
    GPU_COPY: "GPU (NVDEC, copy) — every video on the GPU while the budget lasts",
    GPU_ZERO_COPY: "GPU zero-copy — least CPU, most video memory",
    CPU: "CPU only",
}

# What a tile does when its video ends
LOOP, NEXT, RANDOM, STOP = "loop", "next", "random", "stop"
END_ACTIONS = {
    LOOP: "Loop the video",
    NEXT: "Play the next video in its folder",
    RANDOM: "Play a random video from its folder",
    STOP: "Stop at the end",
}
