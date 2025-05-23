import sqlite3
import numpy as np
from datetime import datetime
from transformers import pipeline
from sentence_transformers import SentenceTransformer, util

class ContentQualityScorer:
    def __init__(self):
        # Модель для оценки информативности
        self.info_model = pipeline("text-classification", 
                                model="cointegrated/rubert-tiny2-cedr-emotion-detection")
        
        # Модель для семантического анализа
        self.semantic_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-mpnet-base-v2')
        
        # Эталонные эмбеддинги для ключевых критериев
        self.quality_embeddings = {
            'informative': self.semantic_model.encode("информативный полезный содержательный"),
            'engaging': self.spectral_model.encode("увлекательный интересный захватывающий"),
            'trustworthy': self.semantic_model.encode("достоверный надежный проверенный")
        }

    def calculate_quality_score(self, text, views, pub_date):
        # Анализ текста
        info_score = self._calculate_information_score(text)
        semantic_score = self._calculate_semantic_score(text)
        
        # Временной коэффициент (новизна)
        time_coef = self._calculate_time_coefficient(pub_date)
        
        # Комбинированная оценка
        return (semantic_score * 0.4 + 
                info_score * 0.3 + 
                np.log1p(views) * 0.2 + 
                time_coef * 0.1)

    def _calculate_information_score(self, text):
        result = self.info_model(text[:512])[0]
        return 1.0 if result['label'] == 'neutral' else 0.5

    def _calculate_semantic_score(self, text):
        text_embedding = self.semantic_model.encode(text)
        scores = []
        for name, emb in self.quality_embeddings.items():
            scores.append(util.pytorch_cos_sim(text_embedding, emb).item())
        return np.mean(scores)

    def _calculate_time_coefficient(self, pub_date):
        days_old = (datetime.now() - datetime.fromisoformat(pub_date)).days
        return max(0, 1 - days_old/30)

def get_most_valuable_post(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    scorer = ContentQualityScorer()

    try:
        # Получаем последние 1000 постов для анализа
        cursor.execute("""
            SELECT title, content, pub_date, views 
            FROM news 
            ORDER BY pub_date DESC 
            LIMIT 1000
        """)
        
        posts = []
        for row in cursor.fetchall():
            title, content, pub_date, views = row
            full_text = f"{title}. {content}"
            
            score = scorer.calculate_quality_score(
                text=full_text,
                views=views,
                pub_date=pub_date
            )
            
            posts.append({
                'title': title,
                'content': content,
                'score': score,
                'pub_date': pub_date,
                'views': views
            })

        # Выбираем пост с максимальным скором
        best_post = max(posts, key=lambda x: x['score'])
        return best_post

    except Exception as e:
        print(f"Error: {str(e)}")
        return None
    finally:
        conn.close()

# Пример использования
if __name__ == "__main__":
    best_post = get_most_valuable_post("database/bee.db")
    print(f"Лучший пост (оценка {best_post['score']:.2f}):")
    print(f"Заголовок: {best_post['title']}")
    print(f"Контент: {best_post['content'][:200]}...")
    print(f"Просмотры: {best_post['views']}")
    print(f"Дата: {best_post['pub_date']}")