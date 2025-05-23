from transformers import GPT2Tokenizer, T5ForConditionalGeneration
import torch
import sqlite3
from datetime import datetime, timedelta

tokenizer = GPT2Tokenizer.from_pretrained('RussianNLP/FRED-T5-Summarizer', eos_token='</s>')
model = T5ForConditionalGeneration.from_pretrained('RussianNLP/FRED-T5-Summarizer')
device = 'cpu'
model.to(device)

db_path = "database/bee.db"
try:
    conn = sqlite3.connect(db_path)

    print(conn)

    cursor = conn.cursor()
    
    yesterday = datetime.now() - timedelta(days=1)
    cursor.execute("""
        SELECT title, content, channel 
        FROM news 
        WHERE pub_date >= ? 
        ORDER BY pub_date DESC
        LIMIT 7
    """, (yesterday.strftime('%Y-%m-%d %H:%M:%S'),))
    
    news_items = cursor.fetchall()
    conn.close()
    
    if not news_items:
        print("На сегодня нет свежих экономических новостей.")
        
    prompt = "Суммаризируй следующие экономические новости:\n\n"
    for title, content, source in news_items:
        prompt += f"Заголовок: {title}\nИсточник: {source}\nТекст: {content[:500]}\n\n"
    
    input_ids = torch.tensor([tokenizer.encode(prompt)]).to(device)
    outputs = model.generate(
        input_ids,
        eos_token_id=tokenizer.eos_token_id,
        num_beams=5,
        min_new_tokens=50,
        max_new_tokens=500,
        do_sample=True,
        no_repeat_ngram_size=4,
        top_p=0.9
    )
    
    summary = tokenizer.decode(outputs[0][1:])
    print( f"📈 Экономическая сводка:\n\n{summary}\n\nИсточники: {', '.join(set(item[2] for item in news_items))}" )
    
except Exception as e:
    print(f"Ошибка при генерации сводки новостей: {e}")
    print( "Произошла ошибка при подготовке экономической сводки. Пожалуйста, попробуйте позже." )