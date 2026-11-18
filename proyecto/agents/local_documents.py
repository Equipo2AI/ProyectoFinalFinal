"""Recuperacion lexical sobre los documentos reales del repositorio, sin red."""
from collections import Counter
from functools import lru_cache
import math
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[2] / 'data_governance_files'
STOP = set('segun ley para por que cual cuales cuanto cuantos tiempo puede banco banamex los las del una uno unos unas como mis mi sus con sin este esta ellos estar sobre entre persona'.split())


def terms(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    words = re.findall(r'[a-z]{3,}', text.replace('citibanamex','banamex'))
    # Raices ligeras para plurales y flexiones, sin descargar modelos linguisticos.
    return {('presentar' if w.startswith('presenta') else w.rstrip('s')[:6]) for w in words if w not in STOP}


@lru_cache(maxsize=12)
def _pages(path, modified):
    p = Path(path)
    if p.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        return [page.extract_text() or '' for page in PdfReader(p).pages]
    return [p.read_text(encoding='utf-8')]


def retrieve(question, k=3):
    paths = sorted(p for p in ROOT.iterdir() if p.suffix.lower() in {'.pdf','.txt'})
    title_terms = terms(question)
    title_scores = [(len(title_terms & terms(p.stem))/max(1,len(terms(p.stem))),p) for p in paths]
    best = max((s for s,p in title_scores), default=0)
    candidates = [p for s,p in title_scores if best == 0 or s >= best * .9]
    focus = question.rsplit('\u00bf',1)[-1]
    query = terms(focus)
    for word, expansions in {'fines':'finalidades propositos', 'utilizar':'tratamiento', 'tiempo':'plazo', 'plazo':'termino'}.items():
        if word in focus.lower():
            query |= terms(expansions)
    if best == 0:
        texts = [p for p in candidates if p.suffix.lower()=='.txt']
        if any(len(query & terms(' '.join(_pages(str(p),p.stat().st_mtime_ns)))) >= 2 for p in texts):
            candidates = texts
    chunks = []
    for p in candidates:
        for page,text in enumerate(_pages(str(p),p.stat().st_mtime_ns),1):
            text = re.sub(r'\s+',' ',text).strip()
            for start in range(0,len(text),1500):
                passage = text[start:start+1800]
                if passage:
                    chunks.append((p.name,page,passage,terms(passage)))
    frequencies = Counter(w for _,_,_,words in chunks for w in words)
    ranked = []
    for name,page,text,words in chunks:
        overlap = query & words
        score = sum(math.log(1+len(chunks)/(1+frequencies[w])) for w in overlap)
        if score:
            ranked.append((score,name,page,text))
    ranked.sort(key=lambda r:r[0],reverse=True)
    # Pasajes literales y ubicaciones reales; el llamador no genera articulos.
    return [{'source':n,'page':p,'text':t} for _,n,p,t in ranked[:k]]
