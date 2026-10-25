import 'package:flutter/material.dart';
import '../services/websocket_client.dart';

class MoodCardsDeck extends StatelessWidget {
  final AetherWebSocketClient wsClient;
  final String activeCardTitle;

  const MoodCardsDeck({
    super.key,
    required this.wsClient,
    required this.activeCardTitle,
  });

  static const List<Map<String, String>> _defaultCards = [
    {
      'card_id': 'card_herta_overdrive',
      'title': 'Curio: High-Voltage Protocol',
      'subtitle': '135–175 BPM • J-Rock, Post-Hardcore & Electro',
      'primary': '0xFF9D4EDD',
      'secondary': '0xFF10002B',
    },
    {
      'card_id': 'card_space_station_nocturne',
      'title': 'Herta Space Station: Midnight Orbit',
      'subtitle': '85–122 BPM • Synthwave, OST & Atmospheric Drift',
      'primary': '0xFF3A0CA3',
      'secondary': '0xFF03071E',
    },
    {
      'card_id': 'card_simulated_chaos',
      'title': 'Simulated Universe: Uncharted Mix',
      'subtitle': 'All BPMs • High Discovery & Genre-Bending',
      'primary': '0xFFF72585',
      'secondary': '0xFF3F37C9',
    },
  ];

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 145,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: _defaultCards.length,
        separatorBuilder: (_, __) => const SizedBox(width: 12),
        itemBuilder: (context, index) {
          final card = _defaultCards[index];
          final isSelected = card['title'] == activeCardTitle;
          return GestureDetector(
            onTap: () {
              wsClient.sendJson({
                'event': 'ACTIVATE_CARD',
                'card_id': card['card_id'],
              });
            },
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: 260,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    Color(int.parse(card['primary']!)),
                    Color(int.parse(card['secondary']!)),
                  ],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                borderRadius: BorderRadius.circular(20),
                border: Border.all(
                  color: isSelected
                      ? const Color(0xFFE0AAFF)
                      : Colors.white.withValues(alpha: 0.12),
                  width: isSelected ? 2.5 : 1.0,
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Icon(Icons.auto_awesome,
                          color: Color(0xFFE0AAFF), size: 20),
                      if (isSelected)
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 2),
                          decoration: BoxDecoration(
                            color: Colors.black45,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: const Text(
                            'ON AIR',
                            style: TextStyle(
                              color: Color(0xFFE0AAFF),
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                    ],
                  ),
                  Text(
                    card['title']!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 15,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(
                    card['subtitle']!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Colors.white70,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
