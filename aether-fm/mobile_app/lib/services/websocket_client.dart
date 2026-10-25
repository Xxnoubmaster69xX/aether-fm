import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:web_socket_channel/web_socket_channel.dart';

const String kBeaconPollUrl =
    'https://ntfy.sh/fm_node_sync_83_d71cd629/raw?poll=1&since=all';

/// Real-time WebSocket client with Zero-Touch LAN + Remote Tunnel Auto-Handshake.
/// 1. Tries local LAN (`ws://192.168.3.85:8000`) first for 1ms home Wi-Fi latency.
/// 2. If outside the house (5G/LTE or external Wi-Fi), automatically fetches the PC's
///    live outbound tunnel URL from the encrypted HTTPS beacon (`ntfy.sh`) and connects
///    via `wss://<remote_host>/ws/session` and `https://<remote_host>/stream.opus`.
class AetherWebSocketClient {
  String tailscaleHost;
  String lanHost;
  String remoteHost;
  bool usingRemoteTunnel = false;
  final int port;
  final String authToken;
  String activeStreamTier;
  String activeListeningProfile;

  WebSocketChannel? _channel;
  bool _disposed = false;
  bool _reconnecting = false;
  String _lastStreamBaseUrl = '';

  final StreamController<Map<String, dynamic>> _eventController =
      StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get events => _eventController.stream;

  AetherWebSocketClient({
    required this.tailscaleHost,
    this.port = 8000,
    this.authToken = 'herta-genius-83-secret-token',
    this.activeStreamTier = 'mobile_saver',
    this.activeListeningProfile = 'flat',
  })  : lanHost = tailscaleHost,
        remoteHost = '';

  bool _isDomainHost(String h) {
    final clean = h
        .replaceFirst('https://', '')
        .replaceFirst('http://', '')
        .replaceFirst('wss://', '')
        .replaceFirst('ws://', '')
        .split('/')
        .first
        .trim();
    return clean.contains('.lhr.life') ||
        clean.contains('.trycloudflare.com') ||
        clean.contains('.ngrok') ||
        RegExp(r'[a-zA-Z]').hasMatch(clean);
  }

  String _cleanHost(String h) {
    return h
        .replaceFirst('https://', '')
        .replaceFirst('http://', '')
        .replaceFirst('wss://', '')
        .replaceFirst('ws://', '')
        .split('/')
        .first
        .split(':')
        .first
        .trim();
  }

  String get activeDisplayNode {
    if (usingRemoteTunnel && remoteHost.isNotEmpty) {
      return 'REMOTE // $remoteHost';
    }
    return 'LAN // $tailscaleHost';
  }

  String get _streamBaseUrl {
    final activeHost =
        (usingRemoteTunnel && remoteHost.isNotEmpty) ? remoteHost : tailscaleHost;
    final clean = _cleanHost(activeHost);
    if (_isDomainHost(clean)) {
      return 'https://$clean';
    }
    return 'http://$clean:$port';
  }

  String _buildWsUrl(String hostCandidate) {
    final clean = _cleanHost(hostCandidate);
    if (_isDomainHost(clean)) {
      return 'wss://$clean/ws/session?token=$authToken';
    }
    return 'ws://$clean:$port/ws/session?token=$authToken';
  }

  String get opusStreamUrl =>
      '$_streamBaseUrl/stream.opus?tier=$activeStreamTier&profile=$activeListeningProfile';

  void updateHost(String newHost) {
    final cleaned = _cleanHost(newHost);
    if (cleaned.isEmpty) return;
    if (_isDomainHost(cleaned)) {
      remoteHost = cleaned;
      usingRemoteTunnel = true;
    } else {
      tailscaleHost = cleaned;
      lanHost = cleaned;
      usingRemoteTunnel = false;
    }
    try {
      _channel?.sink.close();
    } catch (_) {}
    _reconnecting = false;
    connect();
  }

  Future<bool> _tryConnectHost(String hostCandidate, Duration timeout) async {
    try {
      final uri = Uri.parse(_buildWsUrl(hostCandidate));
      final channel = WebSocketChannel.connect(uri);
      await channel.ready.timeout(timeout);
      _channel = channel;
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<String?> _fetchRemoteBeaconHost() async {
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await client.getUrl(Uri.parse(kBeaconPollUrl));
      final resp = await req.close().timeout(const Duration(seconds: 6));
      if (resp.statusCode == 200) {
        final body = await resp.transform(utf8.decoder).join();
        final lines = body
            .split('\n')
            .map((l) => l.trim())
            .where((l) => l.startsWith('{') && l.endsWith('}'))
            .toList();
        for (var i = lines.length - 1; i >= 0; i--) {
          try {
            final map = jsonDecode(lines[i]) as Map<String, dynamic>;
            final rHost = (map['remote_host'] ?? '') as String;
            final lHost = (map['lan_host'] ?? '') as String;
            if (lHost.isNotEmpty) {
              lanHost = lHost;
            }
            if (rHost.isNotEmpty) {
              remoteHost = rHost;
              return rHost;
            }
          } catch (_) {}
        }
      }
    } catch (_) {
    } finally {
      client.close(force: true);
    }
    return remoteHost.isNotEmpty ? remoteHost : null;
  }

  Future<void> connect() async {
    if (_disposed) return;
    bool connected = false;

    // 1. If user explicitly set a domain host or we haven't tried LAN yet:
    if (_isDomainHost(tailscaleHost)) {
      connected = await _tryConnectHost(
        tailscaleHost,
        const Duration(seconds: 5),
      );
      if (connected) {
        remoteHost = _cleanHost(tailscaleHost);
        usingRemoteTunnel = true;
      }
    } else {
      // Try fast local LAN first (2.2s timeout)
      connected = await _tryConnectHost(
        lanHost,
        const Duration(milliseconds: 2200),
      );
      if (connected) {
        tailscaleHost = lanHost;
        usingRemoteTunnel = false;
      }
    }

    // 2. Outside the house (5G / LTE / external Wi-Fi): auto-handshake via beacon!
    if (!connected) {
      final discoveredRemote = await _fetchRemoteBeaconHost();
      if (discoveredRemote != null && discoveredRemote.isNotEmpty) {
        connected = await _tryConnectHost(
          discoveredRemote,
          const Duration(seconds: 6),
        );
        if (connected) {
          usingRemoteTunnel = true;
        } else if (lanHost != tailscaleHost) {
          // Maybe the PC's LAN IP changed on home Wi-Fi; try updated lanHost from beacon
          connected = await _tryConnectHost(
            lanHost,
            const Duration(milliseconds: 2200),
          );
          if (connected) {
            tailscaleHost = lanHost;
            usingRemoteTunnel = false;
          }
        }
      }
    }

    if (!connected || _channel == null) {
      _scheduleReconnect();
      return;
    }

    final channel = _channel!;
    final currentBase = _streamBaseUrl;
    if (_lastStreamBaseUrl != currentBase) {
      _lastStreamBaseUrl = currentBase;
      _eventController.add({
        'event': 'ENDPOINT_CHANGED',
        'stream_url': opusStreamUrl,
        'node_label': activeDisplayNode,
      });
    }

    channel.stream.listen(
      (data) {
        if (data is String) {
          try {
            final decoded = jsonDecode(data) as Map<String, dynamic>;
            _eventController.add(decoded);
          } catch (_) {}
        }
      },
      onDone: _scheduleReconnect,
      onError: (_) => _scheduleReconnect(),
    );

    sendJson({
      'event': 'SESSION_START',
      'client': 'galaxy_s24_ultra',
      'local_time': DateTime.now().toIso8601String(),
    });
  }

  void sendJson(Map<String, dynamic> payload) {
    try {
      _channel?.sink.add(jsonEncode(payload));
    } catch (_) {}
  }

  void sendPcmChunk(Uint8List pcmInt16Bytes) {
    try {
      _channel?.sink.add(pcmInt16Bytes);
    } catch (_) {}
  }

  void _scheduleReconnect() {
    if (_disposed || _reconnecting) return;
    _reconnecting = true;
    Future.delayed(const Duration(seconds: 3), () {
      _reconnecting = false;
      connect();
    });
  }

  void dispose() {
    _disposed = true;
    sendJson({'event': 'SESSION_END'});
    try {
      _channel?.sink.close();
    } catch (_) {}
    _eventController.close();
  }
}
