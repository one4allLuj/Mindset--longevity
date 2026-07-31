import React, { useState } from 'react';

export default function Home() {
  const [quizStarted, setQuizStarted] = useState(false);
  const [step, setStep] = useState(0);
  const [score, setScore] = useState(0);
  const [quizFinished, setQuizFinished] = useState(false);

  const questions = [
    {
      q: "Come valuti la tua chiarezza mentale e concentrazione al mattino?",
      options: [
        { label: "Sento nebbia mentale e stanchezza per le prime 2 ore", pts: 10 },
        { label: "Accettabile, ma ho cali drastici dopo pranzo", pts: 20 },
        { label: "Lucidità immediata ed elevata produttività", pts: 30 }
      ]
    },
    {
      q: "Quante ore di sonno profondo e ristoratore ottieni mediamente?",
      options: [
        { label: "Meno di 5 ore (Mi sveglio spesso stanco)", pts: 10 },
        { label: "Tra 6 e 7 ore (Sufficiente)", pts: 20 },
        { label: "Più di 8 ore di sonno profondo e continuo", pts: 30 }
      ]
    },
    {
      q: "Qual è il tuo obiettivo principale per i prossimi 6 mesi?",
      options: [
        { label: "Eliminare stress e affaticamento cronico", pts: 10 },
        { label: "Massimizzare le performance nel lavoro/studio", pts: 20 },
        { label: "Rallentare l'invecchiamento cellulare (Anti-Aging)", pts: 30 }
      ]
    }
  ];

  const handleAnswer = (pts) => {
    const newScore = score + pts;
    setScore(newScore);
    if (step < questions.length - 1) {
      setStep(step + 1);
    } else {
      setQuizFinished(true);
    }
  };

  return (
    <div style={{ backgroundColor: '#050505', color: '#EDEDED', minHeight: '100vh', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      
      {/* HEADER / NAVBAR */}
      <nav style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '20px 40px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
        <div style={{ fontSize: '24px', fontWeight: '800', letterSpacing: '2px', color: '#FFF' }}>
          MINDSET<span style={{ color: '#00F2FF' }}>™</span>
        </div>
        <div style={{ fontSize: '13px', color: '#888', textTransform: 'uppercase', letterSpacing: '1px' }}>
          Biotecnologie della Longevità • Italia
        </div>
      </nav>

      {/* HERO SECTION */}
      <main style={{ maxWidth: '1100px', margin: '0 auto', padding: '60px 20px', textAlign: 'center' }}>
        
        <div style={{ display: 'inline-block', padding: '6px 16px', borderRadius: '20px', backgroundColor: 'rgba(0, 242, 255, 0.1)', border: '1px solid #00F2FF', color: '#00F2FF', fontSize: '12px', fontWeight: '600', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '24px' }}>
          Scienza Italiana della Longevità
        </div>

        <h1 style={{ fontSize: '54px', fontWeight: '800', lineHeight: '1.1', margin: '0 0 20px 0', letterSpacing: '-1px' }}>
          Il Futuro della tua Mente.<br/>
          <span style={{ background: 'linear-gradient(90deg, #00F2FF, #8B5CF6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Formulato in Italia.
          </span>
        </h1>

        <p style={{ fontSize: '18px', color: '#999', maxWidth: '650px', margin: '0 auto 40px auto', lineHeight: '1.6' }}>
          Ottimizza la concentrazione, elimina la nebbia mentale e proteggi i tuoi neuroni con il primo protocollo di nootropici intelligenti a purezza certificata.
        </p>

        {/* SEZIONE QUIZ INTERATTIVO */}
        <div style={{ backgroundColor: '#111', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '24px', padding: '40px', maxWidth: '650px', margin: '0 auto', boxShadow: '0 20px 50px rgba(0,0,0,0.8)' }}>
          
          {!quizStarted ? (
            <div>
              <h3 style={{ fontSize: '22px', margin: '0 0 10px 0', color: '#FFF' }}>Test dell'Età Biologica & Longevità</h3>
              <p style={{ color: '#777', fontSize: '14px', marginBottom: '24px' }}>Rispondi a 3 domande per calcolare il tuo indice di efficienza cellulare.</p>
              <button 
                onClick={() => setQuizStarted(true)}
                style={{ backgroundColor: '#00F2FF', color: '#000', padding: '16px 36px', borderRadius: '30px', fontWeight: '700', fontSize: '16px', border: 'none', cursor: 'pointer', transition: '0.2s' }}
              >
                Inizia l'Analisi Gratuita →
              </button>
            </div>
          ) : !quizFinished ? (
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontSize: '12px', color: '#00F2FF', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '1px' }}>
                Domanda {step + 1} di {questions.length}
              </div>
              <h3 style={{ fontSize: '20px', color: '#FFF', marginBottom: '20px' }}>{questions[step].q}</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {questions[step].options.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => handleAnswer(opt.pts)}
                    style={{ padding: '16px', borderRadius: '12px', backgroundColor: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: '#DDD', textAlign: 'left', cursor: 'pointer', fontSize: '14px' }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '40px', marginBottom: '10px' }}>🧬</div>
              <h3 style={{ fontSize: '24px', color: '#FFF', margin: '0 0 10px 0' }}>Analisi Completata!</h3>
              <p style={{ color: '#888', fontSize: '14px' }}>Il tuo Punteggio di Efficienza Cellulare è:</p>
              <div style={{ fontSize: '48px', fontWeight: '800', color: '#00F2FF', margin: '10px 0' }}>
                {score} / 90
              </div>
              <div style={{ backgroundColor: 'rgba(139, 92, 246, 0.15)', border: '1px solid #8B5CF6', padding: '15px', borderRadius: '12px', color: '#DDD', fontSize: '14px', marginBottom: '20px' }}>
                <strong>Protocollo Consigliato:</strong> {score < 50 ? 'MINDSET™ Recovery (Rigenerazione Notturna)' : 'MINDSET™ Focus & Longevity (Nootropo Avanzato)'}
              </div>
              <button 
                onClick={() => alert('Registrazione completata! Verrai reindirizzato al checkout riservato.')}
                style={{ backgroundColor: '#8B5CF6', color: '#FFF', padding: '14px 28px', borderRadius: '30px', fontWeight: '700', border: 'none', cursor: 'pointer' }}
              >
                Riscatta la tua Formulazione Personalizzata
              </button>
            </div>
          )}

        </div>

        {/* CARATTERISTICHE PRINCIPALI */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '20px', marginTop: '80px', textAlign: 'left' }}>
          <div style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', padding: '24px', borderRadius: '16px' }}>
            <div style={{ color: '#00F2FF', fontSize: '20px', marginBottom: '10px' }}>⚡ 99.8% Purezza</div>
            <p style={{ color: '#777', fontSize: '14px', margin: 0 }}>Nootropici purificati nei migliori laboratori biotecnologici in Italia.</p>
          </div>
          <div style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', padding: '24px', borderRadius: '16px' }}>
            <div style={{ color: '#8B5CF6', fontSize: '20px', marginBottom: '10px' }}>🚚 Consegna 24h</div>
            <p style={{ color: '#777', fontSize: '14px', margin: 0 }}>Spedizione tracciata e gratuita in tutta Italia dai centri di Bologna.</p>
          </div>
          <div style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', padding: '24px', borderRadius: '16px' }}>
            <div style={{ color: '#00F2FF', fontSize: '20px', marginBottom: '10px' }}>🔬 Dati Clinici</div>
            <p style={{ color: '#777', fontSize: '14px', margin: 0 }}>Formulazioni basate su oltre 40 studi scientifici pubblicati su PubMed.</p>
          </div>
        </div>

      </main>

      {/* FOOTER */}
      <footer style={{ textAlign: 'center', padding: '40px', borderTop: '1px solid rgba(255,255,255,0.05)', color: '#555', fontSize: '12px' }}>
        © {new Date().getFullYear()} MINDSET™ Biotecnologie Italia. Tutti i diritti riservati.
      </footer>

    </div>
  );
}
