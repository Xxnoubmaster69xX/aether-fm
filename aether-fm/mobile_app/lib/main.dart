import 'dart:async';
import 'package:audio_service/audio_service.dart';
import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'services/bluetooth_mic_router.dart';
import 'services/radio_audio_handler.dart';
import 'services/websocket_client.dart';
import 'ui/booth_screen.dart';

const String kHomePcTailscaleIp = '192.168.3.85';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final wsClient = AetherWebSocketClient(tailscaleHost: kHomePcTailscaleIp);
  final micRouter = BluetoothCarMicRouter(wsClient: wsClient);

  AetherRadioAudioHandler audioHandler;
  try {
    audioHandler = await AudioService.init(
      builder: () => AetherRadioAudioHandler(
        wsClient: wsClient,
        micRouter: micRouter,
      ),
      config: const AudioServiceConfig(
        androidNotificationChannelId: 'com.herta.aether_fm.channel.audio',
        androidNotificationChannelName: 'FM Broadcast',
        androidNotificationOngoing: true,
      ),
    );
  } catch (_) {
    audioHandler = AetherRadioAudioHandler(
      wsClient: wsClient,
      micRouter: micRouter,
    );
  }

  runApp(AetherFmApp(wsClient: wsClient, audioHandler: audioHandler));

  unawaited(_bootBackgroundServices(wsClient, audioHandler));
}

Future<void> _bootBackgroundServices(
  AetherWebSocketClient wsClient,
  AetherRadioAudioHandler audioHandler,
) async {
  try {
    await WakelockPlus.enable();
  } catch (_) {}

  wsClient.connect();
  unawaited(audioHandler.startRadioStream());
}

class AetherFmApp extends StatelessWidget {
  final AetherWebSocketClient wsClient;
  final AetherRadioAudioHandler audioHandler;

  const AetherFmApp({
    super.key,
    required this.wsClient,
    required this.audioHandler,
  });

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FM',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: const Color(0xFF0A0A0A),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFFFD600),
          secondary: Color(0xFFFFD600),
          surface: Color(0xFF121212),
        ),
      ),
      home: BoothScreen(
        wsClient: wsClient,
        audioHandler: audioHandler,
      ),
    );
  }
}
