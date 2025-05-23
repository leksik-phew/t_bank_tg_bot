import sqlite3
import feedparser
from datetime import datetime
import requests
from typing import List

class MultiChannelNewsCollector:
    def __init__(self, channels: List[str]):
        self.channels = channels
        self.base_rss_url = "https://tg.i-c-a.su/rss/"
        self.conn = sqlite3.connect('database/bee.db')
        self._init_db()

    def _init_db(self):
        """Инициализация таблицы в базе данных"""
        with self.conn:
            self.conn.execute('''CREATE TABLE IF NOT EXISTS news(
                id INTEGER PRIMARY KEY,
                channel TEXT,
                title TEXT,
                link TEXT UNIQUE,
                content TEXT,
                pub_date DATETIME,
                views INTEGER
            )''')

    def collect_all_news(self):
        """Сбор новостей со всех каналов"""
        for channel in self.channels:
            try:
                self._process_channel(channel)
                print(f"Канал {channel} обработан успешно")
            except Exception as e:
                print(f"Ошибка обработки канала {channel}: {str(e)}")

    def _process_channel(self, channel: str):
        """Обработка одного канала"""
        rss_url = f"{self.base_rss_url}{channel}?limit=100"
        feed = self._fetch_feed(rss_url)
        self._save_feed(channel, feed)

    def _fetch_feed(self, url: str):
        """Получение и парсинг RSS-ленты"""
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return feedparser.parse(response.content)

    def _save_feed(self, channel: str, feed):
        """Сохранение данных из фида в БД"""
        with self.conn:
            for item in feed.entries:
                try:
                    self._save_item(channel, item)
                except Exception as e:
                    print(f"Ошибка сохранения элемента: {str(e)}")

    def _save_item(self, channel: str, item):
        """Сохранение отдельной новости"""
        pub_date = self._parse_datetime(item.published)
        content = self._clean_content(item.description)
        
        self.conn.execute('''INSERT OR IGNORE INTO news 
                          (channel, title, link, content, pub_date, views)
                          VALUES (?, ?, ?, ?, ?, ?)''',
                        (channel,
                         item.title,
                         item.link,
                         content,
                         pub_date,
                         self._extract_views(item)))

    def _parse_datetime(self, date_str: str) -> str:
        """Парсинг даты из различных форматов"""
        formats = [
            '%a, %d %b %Y %H:%M:%S %Z',
            '%Y-%m-%dT%H:%M:%SZ',
            '%a, %d %b %Y %H:%M:%S %z'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).isoformat()
            except ValueError:
                continue
        return datetime.now().isoformat()

    def _clean_content(self, text: str) -> str:
        """Очистка контента от HTML-тегов"""
        return (text.replace('<br/>', '\n')
                  .replace('<br />', '\n')
                  .replace(' ', ' ')
                  .strip())

    def _extract_views(self, item) -> int:
        """Извлечение количества просмотров из описания"""
        description = item.get('description', '')
        if 'Views:' in description:
            return int(description.split('Views:')[-1].split()[0])
        return 0

    def close(self):
        """Закрытие соединения с БД"""
        self.conn.close()

if __name__ == '__main__':
    CHANNELS = [
        'finansist_busines',
        'AlfaBank',
        'sarkari',
        'multievan'
    ]

    collector = MultiChannelNewsCollector(CHANNELS)
    try:
        collector.collect_all_news()
        print("Все новости успешно сохранены!")
    finally:
        collector.close()