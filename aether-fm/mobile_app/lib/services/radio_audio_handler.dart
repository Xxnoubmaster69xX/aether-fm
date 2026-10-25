import 'dart:async';
import 'package:audio_service/audio_service.dart';
import 'package:just_audio/just_audio.dart';
import 'bluetooth_mic_router.dart';
import 'websocket_client.dart';

class AetherRadioAudioHandler extends BaseAudioHandler {
  final AudioPlayer _player = AudioPlayer();
  final AetherWebSocketClient wsClient;
  final BluetoothCarMicRouter micRouter;

  int _prevTapCount = 0;
  Timer? _prevTapTimer;
  DateTime _trackStartTime = DateTime.now();
  bool _isConnectingStream = false;
  bool _userPaused = false;
  Timer? _reconnectTimer;

  bool get isPlaying => _player.playing;

  AetherRadioAudioHandler({
    required this.wsClient,
    required this.micRouter,
  }) {
    _initPlayerMonitoring();
    _listenToServerEvents();
  }

  void _initPlayerMonitoring() {
    _player.playerStateStream.listen((state) {
      if (!_userPaused &&
          (state.processingState == ProcessingState.completed ||
           state.processingState == ProcessingState.idle)) {
        _scheduleStreamReconnect();
      }
    });
    _player.playbackEventStream.listen(
      (_) {},
      onError: (Object e, StackTrace st) {
        if (!_userPaused) {
          _scheduleStreamReconnect();
        }
      },
    );
  }

  void _scheduleStreamReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(milliseconds: 1800), () {
      if (!_userPaused && !_player.playing) {
        startRadioStream(forceReload: true);
      }
    });
  }

  Future<void> setListeningProfile(String profile) async {
    if (wsClient.activeListeningProfile != profile) {
      wsClient.activeListeningProfile = profile;
      if (_player.playing) {
        await startRadioStream(forceReload: true);
      }
    }
  }

  Future<void> startRadioStream({String? tier, bool forceReload = false}) async {
    if (_isConnectingStream) return;
    _isConnectingStream = true;
    _userPaused = false;
    try {
      if (tier != null && tier != wsClient.activeStreamTier) {
        wsClient.activeStreamTier = tier;
        forceReload = true;
      }
      if (_player.playing && !forceReload) {
        return;
      }
      await _player.stop();
      final url = '${wsClient.opusStreamUrl}&t=${DateTime.now().millisecondsSinceEpoch}';
      await _player.setUrl(url).timeout(const Duration(seconds: 12));
      unawaited(_player.play());
      playbackState.add(
        playbackState.value.copyWith(
          playing: true,
          controls: [
            MediaControl.skipToPrevious,
            MediaControl.pause,
            MediaControl.skipToNext,
          ],
          processingState: AudioProcessingState.ready,
        ),
      );
    } catch (_) {
      _scheduleStreamReconnect();
    } finally {
      _isConnectingStream = false;
    }
  }

  Future<void> togglePlayPause() async {
    if (_player.playing) {
      await pause();
    } else {
      await startRadioStream(forceReload: true);
    }
  }

  void _listenToServerEvents() {
    wsClient.events.listen((event) async {
      final type = event['event'];
      if (type == 'ENDPOINT_CHANGED') {
        await startRadioStream(forceReload: true);
      } else if (type == 'NOW_PLAYING' || type == 'STATE') {
        _trackStartTime = DateTime.now();
        final title = event['title'] ?? 'FM Broadcast';
        final artist = event['artist'] ?? 'FM Studio';
        final card = event['card'] ?? '';
        final coverUrl = (event['cover_url'] ?? '') as String;
        mediaItem.add(
          MediaItem(
            id: title,
            title: title,
            artist: card.isNotEmpty ? '$artist • $card' : artist,
            artUri: coverUrl.isNotEmpty ? Uri.parse(coverUrl) : null,
          ),
        );
      } else if (type == 'CALL_MIC_OPEN') {
        await micRouter.openCarBoothMic();
      } else if (type == 'CALL_MIC_CLOSE') {
        await micRouter.releaseCarBoothMicToStereoA2dp();
      }
    });
  }

  @override
  Future<void> skipToNext() async {
    final elapsedSec =
        DateTime.now().difference(_trackStartTime).inMilliseconds / 1000.0;
    wsClient.sendJson({
      'event': 'SKIP_TRACK',
      'source': 'wheel_button',
      'elapsed_sec': elapsedSec,
    });
  }

  @override
  Future<void> skipToPrevious() async {
    _prevTapCount += 1;
    _prevTapTimer?.cancel();

    _prevTapTimer = Timer(const Duration(milliseconds: 800), () {
      if (_prevTapCount == 1) {
        wsClient.sendJson({'event': 'CALL_START', 'sample_rate': 16000});
      } else if (_prevTapCount == 2) {
        wsClient.sendJson({'event': 'CYCLE_MOOD_CARD'});
      } else if (_prevTapCount >= 3) {
        wsClient.sendJson({'event': 'STAR_TRACK'});
      }
      _prevTapCount = 0;
    });
  }

  @override
  Future<void> play() async {
    await startRadioStream(forceReload: false);
  }

  @override
  Future<void> pause() async {
    _userPaused = true;
    _reconnectTimer?.cancel();
    await _player.pause();
    playbackState.add(playbackState.value.copyWith(playing: false));
  }
}
