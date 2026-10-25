import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/services.dart';
import 'package:record/record.dart';
import 'websocket_client.dart';

class BluetoothCarMicRouter {
  static const MethodChannel _androidAudioChannel =
      MethodChannel('aether_fm/car_bluetooth_sco');

  final AetherWebSocketClient wsClient;
  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _micStreamSub;
  Timer? _maxDurationTimer;

  BluetoothCarMicRouter({required this.wsClient});

  Future<void> openCarBoothMic() async {
    _maxDurationTimer?.cancel();
    try {
      await _androidAudioChannel.invokeMethod('startBluetoothSco');
    } catch (_) {}

    try {
      final permitted = await _recorder.hasPermission();
      if (!permitted) {
        wsClient.sendJson({'event': 'CALL_STOP'});
        return;
      }

      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
        ),
      );
      _micStreamSub = stream.listen((Uint8List chunk) {
        wsClient.sendPcmChunk(chunk);
      });

      // Client-side 5.5s safety cutoff so the mic never stays stuck on Listening
      _maxDurationTimer = Timer(const Duration(milliseconds: 5500), () {
        wsClient.sendJson({'event': 'CALL_STOP'});
      });
    } catch (_) {
      wsClient.sendJson({'event': 'CALL_STOP'});
    }
  }

  Future<void> releaseCarBoothMicToStereoA2dp() async {
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    await _micStreamSub?.cancel();
    _micStreamSub = null;
    try {
      await _recorder.stop();
    } catch (_) {}

    try {
      await _androidAudioChannel.invokeMethod('stopBluetoothSco');
    } catch (_) {}
  }
}
