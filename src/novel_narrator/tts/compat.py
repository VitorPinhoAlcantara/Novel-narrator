def apply():
    import soundfile as sf
    import torch
    import torchaudio
    import transformers.pytorch_utils as pytorch_utils

    if not hasattr(pytorch_utils, "isin_mps_friendly"):
        pytorch_utils.isin_mps_friendly = torch.isin

    if getattr(torchaudio.load, "__name__", "") == "_load_with_soundfile":
        return

    def _load_with_soundfile(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, **_ignored):
        start = int(frame_offset) if frame_offset else 0
        frames = -1 if (num_frames is None or num_frames < 0) else int(num_frames)
        data, rate = sf.read(uri, start=start, frames=frames, dtype="float32" if normalize else "int16", always_2d=True)
        tensor = torch.from_numpy(data)
        if channels_first:
            tensor = tensor.T.contiguous()
        return tensor, rate

    torchaudio.load = _load_with_soundfile
