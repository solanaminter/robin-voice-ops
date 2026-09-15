/* Captures mic audio as 16-bit PCM mono chunks and posts them to the main thread.
   The AudioContext is created with sampleRate: 24000 so no resampling is needed —
   the Voice Agent API expects audio/pcm: PCM16 mono @ 24kHz, base64-encoded. */
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input.length > 0) {
      const ch = input[0];
      const pcm = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
