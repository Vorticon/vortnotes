(() => {
  'use strict';
  const timeEl=document.getElementById('vnFocusTime');
  const stateEl=document.getElementById('vnFocusState');
  const statusEl=document.getElementById('vnFocusStatus');
  const orb=document.getElementById('vnFocusOrb');
  const startBtn=document.getElementById('vnFocusStart');
  const resetBtn=document.getElementById('vnFocusReset');
  const minutesEl=document.getElementById('vnFocusMinutes');
  const soundEl=document.getElementById('vnFocusSound');
  const volumeEl=document.getElementById('vnFocusVolume');
  const sessionsEl=document.getElementById('vnFocusSessions');
  const storageKey='vortnotes.ambientFocus.v1';
  let settings={minutes:25,sound:'rain',volume:35,sessions:0};
  try{settings={...settings,...JSON.parse(localStorage.getItem(storageKey)||'{}')};}catch(_){}
  let total=Math.max(60,Number(settings.minutes)*60),remaining=total,running=false,endAt=0,tickId=null,audio=null;
  const save=()=>{settings.minutes=Number(minutesEl.value)||25;settings.sound=soundEl.value;settings.volume=Number(volumeEl.value)||0;try{localStorage.setItem(storageKey,JSON.stringify(settings));}catch(_){}};
  const format=seconds=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  const render=()=>{timeEl.textContent=format(Math.max(0,Math.ceil(remaining)));stateEl.textContent=running?'Focusing':remaining<total?'Paused':'Ready';startBtn.textContent=running?'Pause':remaining<total?'Resume':'Start';orb.style.setProperty('--progress',`${Math.max(0,Math.min(360,(1-remaining/total)*360))}deg`);sessionsEl.textContent=String(settings.sessions||0);document.title=`${timeEl.textContent} · Ambient Focus`;};
  const stopSound=()=>{if(!audio)return;audio.timers.forEach(clearTimeout);audio.nodes.forEach(node=>{try{node.stop();}catch(_){}});try{audio.context.close();}catch(_){}audio=null;};
  const makeSound=()=>{
    stopSound();if(soundEl.value==='none'||!running)return;
    const AudioCtx=window.AudioContext||window.webkitAudioContext;if(!AudioCtx){statusEl.textContent='Ambient audio is not supported in this browser.';return;}
    const context=new AudioCtx(),master=context.createGain(),state={context,gain:master,nodes:[],timers:[]};audio=state;master.gain.value=(Number(volumeEl.value)||0)/100*.42;master.connect(context.destination);
    const noiseBuffer=(mode='white',seconds=3)=>{const length=context.sampleRate*seconds,buffer=context.createBuffer(1,length,context.sampleRate),data=buffer.getChannelData(0);let last=0,b0=0,b1=0,b2=0,b3=0,b4=0,b5=0,b6=0;for(let i=0;i<length;i++){const white=Math.random()*2-1;if(mode==='brown'){last=(last+.02*white)/1.02;data[i]=last*3.5;}else if(mode==='pink'){b0=.99886*b0+white*.0555179;b1=.99332*b1+white*.0750759;b2=.969*b2+white*.153852;b3=.8665*b3+white*.3104856;b4=.55*b4+white*.5329522;b5=-.7616*b5-white*.016898;data[i]=(b0+b1+b2+b3+b4+b5+b6+white*.5362)*.11;b6=white*.115926;}else data[i]=white;}return buffer;};
    const addNoise=(mode,filterType,frequency,level)=>{const source=context.createBufferSource(),filter=context.createBiquadFilter(),gain=context.createGain();source.buffer=noiseBuffer(mode);source.loop=true;filter.type=filterType;filter.frequency.value=frequency;filter.Q.value=.35;gain.gain.value=level;source.connect(filter);filter.connect(gain);gain.connect(master);source.start();state.nodes.push(source);return gain;};
    const addTone=(frequency,level,type='sine')=>{const osc=context.createOscillator(),gain=context.createGain();osc.type=type;osc.frequency.value=frequency;gain.gain.value=level;osc.connect(gain);gain.connect(master);osc.start();state.nodes.push(osc);return {osc,gain};};
    const schedule=(fn,min,max,first=min)=>{const run=()=>{if(audio!==state)return;fn();const id=setTimeout(run,min+Math.random()*(max-min));state.timers.push(id);};const id=setTimeout(run,first);state.timers.push(id);};
    const noiseBurst=(duration,frequency,level)=>{const source=context.createBufferSource(),filter=context.createBiquadFilter(),gain=context.createGain(),now=context.currentTime;source.buffer=noiseBuffer('white',Math.max(.08,duration));filter.type='bandpass';filter.frequency.value=frequency;filter.Q.value=.8;gain.gain.setValueAtTime(.0001,now);gain.gain.exponentialRampToValueAtTime(level,now+.012);gain.gain.exponentialRampToValueAtTime(.0001,now+duration);source.connect(filter);filter.connect(gain);gain.connect(master);source.start(now);source.stop(now+duration+.03);state.nodes.push(source);};
    const bird=()=>{const osc=context.createOscillator(),gain=context.createGain(),now=context.currentTime;osc.type='sine';osc.frequency.setValueAtTime(1200+Math.random()*500,now);osc.frequency.exponentialRampToValueAtTime(2200+Math.random()*700,now+.11);osc.frequency.exponentialRampToValueAtTime(1400+Math.random()*400,now+.28);gain.gain.setValueAtTime(.0001,now);gain.gain.exponentialRampToValueAtTime(.075,now+.025);gain.gain.exponentialRampToValueAtTime(.0001,now+.34);osc.connect(gain);gain.connect(master);osc.start(now);osc.stop(now+.36);state.nodes.push(osc);};
    const thunder=()=>{const osc=context.createOscillator(),gain=context.createGain(),now=context.currentTime;osc.type='sine';osc.frequency.setValueAtTime(52,now);osc.frequency.exponentialRampToValueAtTime(27,now+3.2);gain.gain.setValueAtTime(.0001,now);gain.gain.exponentialRampToValueAtTime(.32,now+.12);gain.gain.exponentialRampToValueAtTime(.0001,now+3.4);osc.connect(gain);gain.connect(master);osc.start(now);osc.stop(now+3.5);state.nodes.push(osc);noiseBurst(2.4,105,.22);};
    const sound=soundEl.value;
    if(sound==='rain')addNoise('white','highpass',650,.72);
    else if(sound==='ocean'){const waves=addNoise('white','lowpass',900,.5),lfo=context.createOscillator(),depth=context.createGain();lfo.frequency.value=.085;depth.gain.value=.28;lfo.connect(depth);depth.connect(waves.gain);lfo.start();state.nodes.push(lfo);}
    else if(sound==='brown')addNoise('brown','lowpass',520,.82);
    else if(sound==='pink')addNoise('pink','lowpass',5200,.82);
    else if(sound==='fireplace'){addNoise('brown','lowpass',1300,.34);schedule(()=>noiseBurst(.05+Math.random()*.12,700+Math.random()*2200,.12+Math.random()*.2),110,650,80);}
    else if(sound==='thunder'){addNoise('white','highpass',700,.48);schedule(thunder,7000,15000,1800+Math.random()*2500);}
    else if(sound==='forest'){addNoise('pink','lowpass',2400,.24);schedule(()=>{bird();if(Math.random()>.55){const id=setTimeout(()=>{if(audio===state)bird();},180);state.timers.push(id);}},1800,5600,700);}
    else if(sound==='coffee'){addNoise('pink','bandpass',520,.38);addNoise('brown','lowpass',240,.2);schedule(()=>noiseBurst(.06,1600+Math.random()*900,.025),900,2600,500);}
    else if(sound==='spaceship'){addNoise('brown','lowpass',260,.2);const engine=addTone(55,.1,'sine');addTone(82,.045,'sine');const lfo=context.createOscillator(),depth=context.createGain();lfo.frequency.value=.12;depth.gain.value=.018;lfo.connect(depth);depth.connect(engine.gain.gain);lfo.start();state.nodes.push(lfo);}
    else if(sound==='vinyl'){addNoise('pink','highpass',1700,.12);schedule(()=>noiseBurst(.015+Math.random()*.025,1800+Math.random()*3500,.06+Math.random()*.11),70,480,90);}
  };
  const chime=()=>{const AudioCtx=window.AudioContext||window.webkitAudioContext;if(!AudioCtx)return;const context=new AudioCtx();[523,659,784].forEach((frequency,index)=>{const osc=context.createOscillator(),gain=context.createGain();osc.frequency.value=frequency;gain.gain.setValueAtTime(.0001,context.currentTime+index*.18);gain.gain.exponentialRampToValueAtTime(.16,context.currentTime+index*.18+.03);gain.gain.exponentialRampToValueAtTime(.0001,context.currentTime+index*.18+.45);osc.connect(gain);gain.connect(context.destination);osc.start(context.currentTime+index*.18);osc.stop(context.currentTime+index*.18+.5);});setTimeout(()=>context.close(),1200);};
  const complete=()=>{running=false;remaining=0;clearInterval(tickId);tickId=null;stopSound();settings.sessions=(Number(settings.sessions)||0)+1;save();statusEl.textContent='Session complete. Nicely done.';chime();render();};
  const tick=()=>{if(!running)return;remaining=Math.max(0,(endAt-Date.now())/1000);if(remaining<=0)complete();else render();};
  const start=()=>{if(running){remaining=Math.max(0,(endAt-Date.now())/1000);running=false;clearInterval(tickId);tickId=null;stopSound();statusEl.textContent='Timer paused.';render();return;}if(remaining<=0)remaining=total;running=true;endAt=Date.now()+remaining*1000;tickId=setInterval(tick,250);statusEl.textContent='';makeSound();render();};
  const reset=()=>{running=false;clearInterval(tickId);tickId=null;stopSound();total=Math.max(60,Math.min(10800,(Number(minutesEl.value)||25)*60));remaining=total;statusEl.textContent='';save();render();};
  startBtn.addEventListener('click',start);resetBtn.addEventListener('click',reset);
  document.querySelectorAll('[data-focus-minutes]').forEach(btn=>btn.addEventListener('click',()=>{minutesEl.value=btn.dataset.focusMinutes;reset();}));
  minutesEl.addEventListener('change',reset);soundEl.addEventListener('change',()=>{save();if(running)makeSound();});volumeEl.addEventListener('input',()=>{save();if(audio)audio.gain.gain.value=(Number(volumeEl.value)||0)/100*.42;});
  window.addEventListener('beforeunload',stopSound);
  minutesEl.value=String(settings.minutes);soundEl.value=settings.sound;volumeEl.value=String(settings.volume);total=Math.max(60,Number(settings.minutes)*60);remaining=total;render();
})();
