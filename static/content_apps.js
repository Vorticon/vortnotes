(() => {
  'use strict';
  const app = window.VORTNOTES_CONTENT_APP;
  const canvas = document.getElementById('vnGameCanvas');
  const scoreEl = document.getElementById('vnGameScore');
  const extraEl = document.getElementById('vnGameExtra');
  const messageEl = document.getElementById('vnGameMessage');
  const restart = document.getElementById('vnGameRestart');
  const difficultyEl = document.getElementById('vnGameDifficulty');
  const highScoreForm = document.getElementById('vnHighScoreForm');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let currentScore = 0;
  let gameEnded = false;
  const setScore = (score) => { currentScore = score; scoreEl.textContent = String(score); };
  const setMessage = (text) => { messageEl.textContent = text || ''; };

  const difficulty = () => difficultyEl?.value || 'medium';
  const highScoreKey = () => `vortnotes.highScores.${app}.${difficulty()}.v2`;
  const setGameEnded = ended => {
    gameEnded = ended;
    if (highScoreForm) highScoreForm.hidden = !ended;
  };
  const readHighScores = () => {
    try { return JSON.parse(localStorage.getItem(highScoreKey()) || '[]').filter(x => x && Number.isFinite(x.score)); }
    catch (_) { return []; }
  };
  const renderHighScores = () => {
    const list = document.getElementById('vnHighScoreList');
    if (!list) return;
    const label = document.getElementById('vnHighScoreDifficulty');
    if (label) label.textContent = difficulty()[0].toUpperCase() + difficulty().slice(1);
    const scores = readHighScores().sort((a,b) => b.score - a.score).slice(0,10);
    list.innerHTML = '';
    if (!scores.length){
      const empty = document.createElement('li'); empty.className = 'vn-empty-score'; empty.textContent = 'No scores yet.'; list.appendChild(empty); return;
    }
    scores.forEach(entry => {
      const li = document.createElement('li');
      const initials = document.createElement('strong'); initials.textContent = entry.initials;
      const points = document.createElement('span'); points.textContent = entry.score.toLocaleString();
      li.append(initials, points); list.appendChild(li);
    });
  };
  document.getElementById('vnHighScoreInitials')?.addEventListener('input', e => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0,3);
  });
  document.getElementById('vnHighScoreForm')?.addEventListener('submit', e => {
    e.preventDefault();
    const input = document.getElementById('vnHighScoreInitials');
    const initials = (input?.value || '').trim().toUpperCase();
    if (!/^[A-Z0-9]{1,3}$/.test(initials)){ setMessage('Enter one to three initials first.'); input?.focus(); return; }
    if (!gameEnded){ setMessage('Finish the game before saving a high score.'); return; }
    if (currentScore <= 0){ setMessage('Score some points before saving.'); return; }
    const scores = readHighScores(); scores.push({initials, score: currentScore, at: Date.now()});
    scores.sort((a,b) => b.score - a.score);
    try { localStorage.setItem(highScoreKey(), JSON.stringify(scores.slice(0,10))); } catch (_) {}
    renderHighScores(); setGameEnded(false); setMessage(`Saved ${initials} — ${currentScore.toLocaleString()} points.`);
  });
  document.getElementById('vnClearHighScores')?.addEventListener('click', () => {
    const label=difficulty()[0].toUpperCase()+difficulty().slice(1);
    if(!window.confirm(`Clear the ${label} scoreboard for this game?`))return;
    try{localStorage.removeItem(highScoreKey());}catch(_){}
    renderHighScores();setMessage(`${label} scoreboard cleared.`);
  });
  renderHighScores();

  function startTetris(){
    const COLS = 10, ROWS = 20, CELL = 30;
    const DIFFICULTIES = {
      easy: {drop: 850, multiplier: 1},
      medium: {drop: 650, multiplier: 1.5},
      hard: {drop: 430, multiplier: 2}
    };
    const COLORS = [null, '#45d6ff', '#ffd84a', '#b769ff', '#54df79', '#ff5f75', '#5f7cff', '#ff9c42'];
    const SHAPES = [
      [[1,1,1,1]], [[2,2],[2,2]], [[0,3,0],[3,3,3]], [[0,4,4],[4,4,0]],
      [[5,5,0],[0,5,5]], [[6,0,0],[6,6,6]], [[0,0,7],[7,7,7]]
    ];
    canvas.width = COLS * CELL; canvas.height = ROWS * CELL;
    let board, piece, score, lines, over, last, dropMs, raf;
    const clone = shape => shape.map(row => row.slice());
    const newPiece = () => { const shape=clone(SHAPES[Math.floor(Math.random()*SHAPES.length)]); return {shape,x:Math.floor((COLS-shape[0].length)/2),y:-1}; };
    const collides = (p,dx=0,dy=0,shape=p.shape) => shape.some((row,y)=>row.some((v,x)=>{if(!v)return false;const bx=p.x+x+dx,by=p.y+y+dy;return bx<0||bx>=COLS||by>=ROWS||(by>=0&&board[by][bx]);}));
    const rotate = () => { if(over)return;const next=piece.shape[0].map((_,i)=>piece.shape.map(row=>row[i]).reverse());for(const nudge of [0,-1,1,-2,2])if(!collides(piece,nudge,0,next)){piece.x+=nudge;piece.shape=next;break;} };
    const merge = () => piece.shape.forEach((row,y)=>row.forEach((v,x)=>{const by=piece.y+y;if(v&&by>=0)board[by][piece.x+x]=v;}));
    const clearLines = () => { let cleared=0;for(let y=ROWS-1;y>=0;y--)if(board[y].every(Boolean)){board.splice(y,1);board.unshift(Array(COLS).fill(0));cleared++;y++;}if(cleared){const settings=DIFFICULTIES[difficulty()];lines+=cleared;score+=Math.round([0,100,300,500,800][cleared]*settings.multiplier);dropMs=Math.max(90,settings.drop-lines*12);setScore(score);extraEl.textContent=`Lines ${lines} · ${settings.multiplier}× points`;} };
    const lock = () => {merge();clearLines();piece=newPiece();if(collides(piece)){over=true;setGameEnded(true);setMessage('Game over — add your initials to save this score.');}};
    const down = () => {if(over)return;if(!collides(piece,0,1))piece.y++;else lock();};
    const move = dx => {if(!over&&!collides(piece,dx,0))piece.x+=dx;};
    const hardDrop = () => {if(over)return;const multiplier=DIFFICULTIES[difficulty()].multiplier;while(!collides(piece,0,1)){piece.y++;score+=Math.round(2*multiplier);}setScore(score);lock();};
    const block = (x,y,v) => {ctx.fillStyle=COLORS[v];ctx.fillRect(x*CELL+1,y*CELL+1,CELL-2,CELL-2);ctx.fillStyle='rgba(255,255,255,.22)';ctx.fillRect(x*CELL+4,y*CELL+4,CELL-8,4);};
    const draw = () => {ctx.fillStyle='#090b13';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.strokeStyle='rgba(255,255,255,.045)';for(let x=0;x<=COLS;x++){ctx.beginPath();ctx.moveTo(x*CELL,0);ctx.lineTo(x*CELL,canvas.height);ctx.stroke();}for(let y=0;y<=ROWS;y++){ctx.beginPath();ctx.moveTo(0,y*CELL);ctx.lineTo(canvas.width,y*CELL);ctx.stroke();}board.forEach((row,y)=>row.forEach((v,x)=>{if(v)block(x,y,v);}));piece.shape.forEach((row,y)=>row.forEach((v,x)=>{if(v&&piece.y+y>=0)block(piece.x+x,piece.y+y,v);}));};
    const frame = (now=0) => {if(!last)last=now;if(!over&&now-last>dropMs){down();last=now;}draw();raf=requestAnimationFrame(frame);};
    const action = name => ({left:()=>move(-1),right:()=>move(1),rotate,down,drop:hardDrop}[name]?.());
    const reset = () => {const settings=DIFFICULTIES[difficulty()];cancelAnimationFrame(raf);board=Array.from({length:ROWS},()=>Array(COLS).fill(0));piece=newPiece();score=0;lines=0;over=false;last=0;dropMs=settings.drop;setScore(0);setGameEnded(false);extraEl.textContent=`Lines 0 · ${settings.multiplier}× points`;setMessage('');renderHighScores();frame();};
    document.addEventListener('keydown',e=>{const map={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'rotate',ArrowDown:'down',' ':'drop'};if(map[e.key]){e.preventDefault();action(map[e.key]);}});
    document.querySelectorAll('[data-game-action]').forEach(btn=>btn.addEventListener('pointerdown',e=>{e.preventDefault();action(btn.dataset.gameAction);}));
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  function startJewels(){
    const SIZE=8, CELL=58, TYPES=6, PRISM=99;
    const DIFFICULTIES={
      easy:{moves:45,multiplier:1,prismRun:4},
      medium:{moves:35,multiplier:1.5,prismRun:4},
      hard:{moves:25,multiplier:2,prismRun:5}
    };
    const COLORS=['#E69F00','#56B4E9','#009E73','#F0E442','#0072B2','#CC79A7'];
    const SYMBOLS=['●','│','—','✕','＋','○'];
    const reducedMotion=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    canvas.width=SIZE*CELL;canvas.height=SIZE*CELL;
    let board,selected,score,moves,busy,settings;
    const key=p=>`${p.x},${p.y}`;
    const adjacent=(a,b)=>a&&b&&Math.abs(a.x-b.x)+Math.abs(a.y-b.y)===1;
    const findRuns=()=>{
      const runs=[];
      for(let y=0;y<SIZE;y++)for(let x=0;x<SIZE;){const v=board[y][x];let n=1;while(v!==null&&v!==PRISM&&x+n<SIZE&&board[y][x+n]===v)n++;if(v!==null&&v!==PRISM&&n>=3)runs.push(Array.from({length:n},(_,i)=>({x:x+i,y})));x+=n;}
      for(let x=0;x<SIZE;x++)for(let y=0;y<SIZE;){const v=board[y][x];let n=1;while(v!==null&&v!==PRISM&&y+n<SIZE&&board[y+n][x]===v)n++;if(v!==null&&v!==PRISM&&n>=3)runs.push(Array.from({length:n},(_,i)=>({x,y:y+i})));y+=n;}
      return runs;
    };
    const swap=(a,b)=>{const t=board[a.y][a.x];board[a.y][a.x]=board[b.y][b.x];board[b.y][b.x]=t;};
    const drawOrb=(x,y,v,alpha=1)=>{
      const cx=x*CELL+CELL/2,cy=y*CELL+CELL/2,r=CELL*.35;ctx.save();ctx.globalAlpha=alpha;
      if(v===PRISM){const rainbow=ctx.createConicGradient(0,cx,cy);['#ff5b62','#ffd84a','#42df9b','#56b4e9','#a77bff','#ff5b62'].forEach((c,i)=>rainbow.addColorStop(i/5,c));ctx.fillStyle=rainbow;}
      else{const g=ctx.createRadialGradient(cx-r*.35,cy-r*.4,r*.05,cx,cy,r);g.addColorStop(0,'#fff');g.addColorStop(.18,COLORS[v]);g.addColorStop(.72,COLORS[v]);g.addColorStop(1,'#101526');ctx.fillStyle=g;}
      ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fill();ctx.strokeStyle='rgba(255,255,255,.7)';ctx.lineWidth=2;ctx.stroke();
      ctx.fillStyle='rgba(255,255,255,.9)';ctx.beginPath();ctx.arc(cx-r*.34,cy-r*.38,r*.12,0,Math.PI*2);ctx.fill();
      ctx.fillStyle=v===PRISM?'#fff':'rgba(10,14,25,.78)';ctx.font=`bold ${v===PRISM?22:18}px system-ui`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(v===PRISM?'✦':SYMBOLS[v],cx,cy+1);ctx.restore();
    };
    const draw=(skip=new Set())=>{ctx.fillStyle='#090b13';ctx.fillRect(0,0,canvas.width,canvas.height);for(let y=0;y<SIZE;y++)for(let x=0;x<SIZE;x++){ctx.fillStyle=(x+y)%2?'rgba(255,255,255,.022)':'rgba(255,255,255,.045)';ctx.fillRect(x*CELL,y*CELL,CELL,CELL);if(board[y][x]!==null&&!skip.has(`${x},${y}`))drawOrb(x,y,board[y][x]);}if(selected){ctx.strokeStyle='#fff';ctx.lineWidth=3;ctx.setLineDash([6,5]);ctx.strokeRect(selected.x*CELL+4,selected.y*CELL+4,CELL-8,CELL-8);ctx.setLineDash([]);}};
    const animateSwap=(a,b)=>new Promise(resolve=>{const va=board[a.y][a.x],vb=board[b.y][b.x],duration=reducedMotion?0:190,start=performance.now(),skip=new Set([key(a),key(b)]);const tick=now=>{const t=duration?Math.min(1,(now-start)/duration):1;const eased=1-Math.pow(1-t,3);draw(skip);drawOrb(a.x+(b.x-a.x)*eased,a.y+(b.y-a.y)*eased,va);drawOrb(b.x+(a.x-b.x)*eased,b.y+(a.y-b.y)*eased,vb);if(t<1)requestAnimationFrame(tick);else{swap(a,b);draw();resolve();}};requestAnimationFrame(tick);});
    const settle=()=>{for(let x=0;x<SIZE;x++){const vals=[];for(let y=SIZE-1;y>=0;y--)if(board[y][x]!==null)vals.push(board[y][x]);for(let y=SIZE-1,i=0;y>=0;y--,i++)board[y][x]=i<vals.length?vals[i]:Math.floor(Math.random()*TYPES);}};
    const resolveMatches=async(preferred=null)=>{busy=true;let chain=0;while(true){const runs=findRuns();if(!runs.length)break;chain++;const hit=new Set(),specials=new Map();runs.forEach(run=>{run.forEach(p=>hit.add(key(p)));if(run.length>=settings.prismRun){const preferredKey=preferred&&run.some(p=>key(p)===key(preferred))?key(preferred):key(run[Math.floor(run.length/2)]);specials.set(preferredKey,PRISM);}});specials.forEach((_,k)=>hit.delete(k));hit.forEach(k=>{const [x,y]=k.split(',').map(Number);board[y][x]=null;});specials.forEach((v,k)=>{const [x,y]=k.split(',').map(Number);board[y][x]=v;});score+=Math.round(((hit.size*10*chain)+(specials.size*100))*settings.multiplier);setScore(score);draw();await new Promise(r=>setTimeout(r,reducedMotion?0:180));settle();draw();await new Promise(r=>setTimeout(r,reducedMotion?0:140));preferred=null;}busy=false;};
    const activatePrism=async(a,b,va,vb)=>{await animateSwap(a,b);const color=va===PRISM?vb:va;let cleared=0;for(let y=0;y<SIZE;y++)for(let x=0;x<SIZE;x++)if(color===PRISM||board[y][x]===color||board[y][x]===PRISM&&((va===PRISM&&x===b.x&&y===b.y)||(vb===PRISM&&x===a.x&&y===a.y))){board[y][x]=null;cleared++;}score+=Math.round(cleared*25*settings.multiplier);setScore(score);draw();await new Promise(r=>setTimeout(r,reducedMotion?0:220));settle();draw();await resolveMatches();setMessage(`Prism burst cleared ${cleared} orbs!`);};
    const finishMove=()=>{const remaining=Math.max(0,settings.moves-moves);extraEl.textContent=`Moves left ${remaining} · ${settings.multiplier}× points`;if(remaining===0){busy=true;setGameEnded(true);setMessage('Game over — add your initials to save this score.');return true;}return false;};
    const choose=async(x,y)=>{if(busy||gameEnded)return;if(!selected){selected={x,y};draw();return;}const next={x,y};if(!adjacent(selected,next)){selected=next;draw();return;}const first=selected;selected=null;busy=true;const va=board[first.y][first.x],vb=board[next.y][next.x];if(va===PRISM||vb===PRISM){moves++;await activatePrism(first,next,va,vb);busy=false;finishMove();return;}await animateSwap(first,next);if(!findRuns().length){await new Promise(r=>setTimeout(r,reducedMotion?0:80));await animateSwap(first,next);setMessage('That swap does not make a match.');busy=false;}else{moves++;setMessage('');busy=false;await resolveMatches(next);draw();finishMove();}};
    const reset=()=>{settings=DIFFICULTIES[difficulty()];score=0;moves=0;selected=null;busy=false;setScore(0);setGameEnded(false);extraEl.textContent=`Moves left ${settings.moves} · ${settings.multiplier}× points`;setMessage(`${difficulty()[0].toUpperCase()+difficulty().slice(1)}: prism orbs require a match of ${settings.prismRun}.`);renderHighScores();do{board=Array.from({length:SIZE},()=>Array.from({length:SIZE},()=>Math.floor(Math.random()*TYPES)));}while(findRuns().length);draw();};
    canvas.addEventListener('pointerdown',e=>{const r=canvas.getBoundingClientRect(),x=Math.floor((e.clientX-r.left)*canvas.width/r.width/CELL),y=Math.floor((e.clientY-r.top)*canvas.height/r.height/CELL);if(x>=0&&x<SIZE&&y>=0&&y<SIZE)choose(x,y);});
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  function startMemory(){
    const DIFFICULTIES={
      easy:{cols:4,rows:4,multiplier:1},
      medium:{cols:6,rows:4,multiplier:1.5},
      hard:{cols:6,rows:6,multiplier:2}
    };
    const SYMBOLS=['🌙','⭐','☀️','🌈','🍀','🌸','🍄','🦋','🐳','🦊','🐙','🦉','🚀','🎵','💎','⚡','🔥','❄️'];
    const stage=canvas.parentElement;
    canvas.style.display='none';
    const grid=document.createElement('div');
    grid.className='vn-memory-grid';
    stage.appendChild(grid);
    let first=null,second=null,locked=false,moves=0,matched=0,score=0,streak=0,settings;
    const shuffle=items=>{for(let i=items.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[items[i],items[j]]=[items[j],items[i]];}return items;};
    const updateStats=()=>{extraEl.textContent=`Moves ${moves} · Pairs ${matched}/${settings.cols*settings.rows/2} · ${settings.multiplier}× points`;};
    const finishTurn=()=>{first=null;second=null;locked=false;};
    const flip=card=>{
      if(locked||card===first||card.classList.contains('is-matched')||gameEnded)return;
      card.classList.add('is-flipped');card.setAttribute('aria-label',`Tile ${card.dataset.symbol}`);
      if(!first){first=card;return;}
      second=card;locked=true;moves++;
      if(first.dataset.pair===second.dataset.pair){
        first.classList.add('is-matched');second.classList.add('is-matched');
        first.setAttribute('aria-label',`Matched ${first.dataset.symbol}`);second.setAttribute('aria-label',`Matched ${second.dataset.symbol}`);
        matched++;streak++;score+=Math.round((100+streak*20)*settings.multiplier);setScore(score);updateStats();
        const total=settings.cols*settings.rows/2;
        if(matched===total){setGameEnded(true);setMessage(`All ${total} pairs found in ${moves} moves — add your initials!`);locked=true;}
        else{setMessage(streak>1?`${streak} matches in a row!`:'Match!');finishTurn();}
      }else{
        streak=0;score=Math.max(0,score-Math.round(10*settings.multiplier));setScore(score);updateStats();setMessage('Not a match — remember those tiles.');
        const a=first,b=second;setTimeout(()=>{a.classList.remove('is-flipped');b.classList.remove('is-flipped');a.setAttribute('aria-label','Hidden tile');b.setAttribute('aria-label','Hidden tile');finishTurn();},650);
      }
    };
    const reset=()=>{
      settings=DIFFICULTIES[difficulty()];first=null;second=null;locked=false;moves=0;matched=0;score=0;streak=0;setScore(0);setGameEnded(false);setMessage('Flip a tile to begin.');renderHighScores();grid.innerHTML='';grid.style.gridTemplateColumns=`repeat(${settings.cols},1fr)`;
      const pairs=settings.cols*settings.rows/2;
      const deck=shuffle(Array.from({length:pairs},(_,i)=>[i,i]).flat());
      deck.forEach(pair=>{const symbol=SYMBOLS[pair];const card=document.createElement('button');card.type='button';card.className='vn-memory-card';card.dataset.pair=String(pair);card.dataset.symbol=symbol;card.setAttribute('aria-label','Hidden tile');card.innerHTML=`<span class="vn-memory-face vn-memory-front" aria-hidden="true">?</span><span class="vn-memory-face vn-memory-back" aria-hidden="true">${symbol}</span>`;card.addEventListener('click',()=>flip(card));grid.appendChild(card);});
      updateStats();
    };
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  function startMinesweeper(){
    const DIFFICULTIES={easy:{rows:9,cols:9,mines:10,multiplier:1},medium:{rows:12,cols:12,mines:24,multiplier:1.5},hard:{rows:16,cols:16,mines:45,multiplier:2}};
    const stage=canvas.parentElement;canvas.style.display='none';
    const grid=document.createElement('div');grid.className='vn-mines-grid';stage.appendChild(grid);
    let settings,board,started,finished,opened,flags,startTime,longPress;
    const neighbors=(r,c)=>{const out=[];for(let dr=-1;dr<=1;dr++)for(let dc=-1;dc<=1;dc++){if(!dr&&!dc)continue;const nr=r+dr,nc=c+dc;if(nr>=0&&nr<settings.rows&&nc>=0&&nc<settings.cols)out.push([nr,nc]);}return out;};
    const updateStats=()=>{extraEl.textContent=`Mines ${settings.mines} · Flags ${flags} · ${settings.multiplier}× points`;};
    const placeMines=(safeR,safeC)=>{const forbidden=new Set([[safeR,safeC],...neighbors(safeR,safeC)].map(([r,c])=>`${r},${c}`));const spots=[];for(let r=0;r<settings.rows;r++)for(let c=0;c<settings.cols;c++)if(!forbidden.has(`${r},${c}`))spots.push([r,c]);for(let i=spots.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[spots[i],spots[j]]=[spots[j],spots[i]];}spots.slice(0,settings.mines).forEach(([r,c])=>board[r][c].mine=true);for(let r=0;r<settings.rows;r++)for(let c=0;c<settings.cols;c++)board[r][c].adj=neighbors(r,c).filter(([nr,nc])=>board[nr][nc].mine).length;started=true;startTime=Date.now();};
    const paint=()=>{grid.querySelectorAll('.vn-mine-cell').forEach(btn=>{const cell=board[+btn.dataset.r][+btn.dataset.c];btn.className='vn-mine-cell';btn.textContent='';if(cell.open){btn.classList.add('is-open');if(cell.mine){btn.classList.add('is-mine');btn.textContent='✹';}else if(cell.adj){btn.textContent=String(cell.adj);btn.classList.add(`vn-number-${Math.min(cell.adj,6)}`);}}else if(cell.flagged){btn.classList.add('is-flagged');btn.textContent='⚑';}});};
    const end=(won)=>{finished=true;setGameEnded(true);if(!won)board.flat().forEach(cell=>{if(cell.mine)cell.open=true;});else{const elapsed=Math.floor((Date.now()-startTime)/1000);const bonus=Math.max(0,1200-elapsed*5);setScore(currentScore+Math.round(bonus*settings.multiplier));}paint();setMessage(won?'Field cleared — add your initials to save the score!':'Mine triggered — game over. You can save your score.');};
    const checkWin=()=>{if(opened===settings.rows*settings.cols-settings.mines)end(true);};
    const reveal=(r,c)=>{if(finished)return;const cell=board[r][c];if(cell.open||cell.flagged)return;if(!started)placeMines(r,c);cell.open=true;opened++;if(cell.mine){end(false);return;}setScore(currentScore+Math.round(10*settings.multiplier));if(cell.adj===0)neighbors(r,c).forEach(([nr,nc])=>reveal(nr,nc));paint();checkWin();};
    const flag=(r,c)=>{if(finished)return;const cell=board[r][c];if(cell.open)return;cell.flagged=!cell.flagged;flags+=cell.flagged?1:-1;paint();updateStats();};
    const reset=()=>{settings=DIFFICULTIES[difficulty()];board=Array.from({length:settings.rows},()=>Array.from({length:settings.cols},()=>({mine:false,open:false,flagged:false,adj:0})));started=false;finished=false;opened=0;flags=0;setScore(0);setGameEnded(false);setMessage('Your first tile is always safe.');renderHighScores();grid.innerHTML='';grid.style.gridTemplateColumns=`repeat(${settings.cols},1fr)`;for(let r=0;r<settings.rows;r++)for(let c=0;c<settings.cols;c++){const btn=document.createElement('button');btn.type='button';btn.className='vn-mine-cell';btn.dataset.r=r;btn.dataset.c=c;btn.setAttribute('aria-label','Hidden field tile');btn.addEventListener('click',()=>reveal(r,c));btn.addEventListener('contextmenu',e=>{e.preventDefault();flag(r,c);});btn.addEventListener('pointerdown',e=>{if(e.pointerType!=='mouse')longPress=setTimeout(()=>flag(r,c),550);});btn.addEventListener('pointerup',()=>clearTimeout(longPress));btn.addEventListener('pointercancel',()=>clearTimeout(longPress));grid.appendChild(btn);}updateStats();};
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  function startBreakout(){
    const W=640,H=440;
    const DIFFICULTIES={easy:{rows:4,speed:4,paddle:120,lives:5,powerChance:.24,multiplier:1},medium:{rows:5,speed:5,paddle:96,lives:3,powerChance:.17,multiplier:1.5},hard:{rows:6,speed:6.2,paddle:76,lives:2,powerChance:.11,multiplier:2}};
    canvas.width=W;canvas.height=H;
    let settings,paddle,ball,bricks,powerups,lives,score,level,raf,last=0,left=false,right=false,expandUntil=0,slowUntil=0;
    const colors=['#ff5f75','#ff9f43','#ffd84a','#54df79','#45c9d6','#6f8cff'];
    const levelSpeed=()=>settings.speed*(1+(level-1)*.045);
    const normalizeBall=speed=>{const mag=Math.hypot(ball.vx,ball.vy)||1;ball.vx=ball.vx/mag*speed;ball.vy=ball.vy/mag*speed;};
    const makeBricks=()=>{const cols=10,rows=settings.rows+Math.min(3,Math.floor((level-1)/3)),gap=6,margin=22,bw=(W-margin*2-gap*(cols-1))/cols,bh=20;return Array.from({length:rows},(_,r)=>Array.from({length:cols},(_,c)=>{const armored=level>=4&&(r+c+level)%4===0;const reinforced=level>=8&&(r*3+c+level)%7===0;const hp=1+(armored?1:0)+(reinforced?1:0);return{x:margin+c*(bw+gap),y:38+r*(bh+gap),w:bw,h:bh,alive:true,hp,maxHp:hp,color:colors[(r+level-1)%colors.length]};})).flat();};
    const resetBall=()=>{const speed=levelSpeed();ball={x:W/2,y:H-58,r:8,vx:speed*(Math.random()>.5?1:-1),vy:-speed};slowUntil=0;};
    const updateStats=()=>{extraEl.textContent=`Level ${level}/10 · Lives ${lives} · Bricks ${bricks.filter(b=>b.alive).length} · ${settings.multiplier}× points`;};
    const end=won=>{setGameEnded(true);setMessage(won?'All 10 levels cleared — add your initials to save the score!':'Out of lives — game over. Add your initials to save the score.');};
    const spawnPowerup=b=>{if(Math.random()>=settings.powerChance)return;const types=['expand','slow','life'];powerups.push({x:b.x+b.w/2,y:b.y+b.h/2,vy:2.1,type:types[Math.floor(Math.random()*types.length)]});};
    const applyPowerup=power=>{if(power.type==='expand'){paddle.w=Math.min(settings.paddle*1.65,W*.38);expandUntil=Date.now()+12000;setMessage('Wide paddle for 12 seconds!');}else if(power.type==='slow'){normalizeBall(levelSpeed()*.68);slowUntil=Date.now()+10000;setMessage('Ball slowed for 10 seconds!');}else{lives=Math.min(lives+1,settings.lives+3);setMessage('Extra life!');}score+=Math.round(125*settings.multiplier);setScore(score);updateStats();};
    const draw=()=>{ctx.fillStyle='#080b14';ctx.fillRect(0,0,W,H);ctx.fillStyle='#e9efff';ctx.fillRect(paddle.x,paddle.y,paddle.w,paddle.h);bricks.forEach(b=>{if(!b.alive)return;ctx.fillStyle=b.color;ctx.globalAlpha=.62+.38*(b.hp/b.maxHp);ctx.fillRect(b.x,b.y,b.w,b.h);ctx.globalAlpha=1;ctx.fillStyle='rgba(255,255,255,.28)';ctx.fillRect(b.x+3,b.y+3,b.w-6,3);if(b.hp>1){ctx.fillStyle='rgba(10,12,20,.65)';ctx.font='bold 12px system-ui';ctx.textAlign='center';ctx.fillText(String(b.hp),b.x+b.w/2,b.y+15);}});powerups.forEach(p=>{const color=p.type==='expand'?'#66e0ff':p.type==='slow'?'#b388ff':'#70e58b';ctx.fillStyle=color;ctx.beginPath();ctx.arc(p.x,p.y,11,0,Math.PI*2);ctx.fill();ctx.fillStyle='#101522';ctx.font='bold 11px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(p.type==='expand'?'↔':p.type==='slow'?'S':'+1',p.x,p.y);});const glow=ctx.createRadialGradient(ball.x-2,ball.y-3,1,ball.x,ball.y,ball.r*1.8);glow.addColorStop(0,'#fff');glow.addColorStop(.35,'#77ddff');glow.addColorStop(1,'rgba(70,160,255,0)');ctx.fillStyle=glow;ctx.beginPath();ctx.arc(ball.x,ball.y,ball.r*1.8,0,Math.PI*2);ctx.fill();ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(ball.x,ball.y,ball.r,0,Math.PI*2);ctx.fill();};
    const nextLevel=()=>{score+=Math.round((level*300+lives*150)*settings.multiplier);setScore(score);if(level>=10){end(true);return;}level++;bricks=makeBricks();powerups=[];paddle.w=settings.paddle;expandUntil=0;resetBall();updateStats();setMessage(`Level ${level} — tougher bricks and a faster ball.`);};
    const update=dt=>{if(gameEnded)return;const scale=Math.min(2,dt/16.67),now=Date.now();if(expandUntil&&now>expandUntil){paddle.w=settings.paddle;expandUntil=0;}if(slowUntil&&now>slowUntil){normalizeBall(levelSpeed());slowUntil=0;}if(left)paddle.x-=8*scale;if(right)paddle.x+=8*scale;paddle.x=Math.max(0,Math.min(W-paddle.w,paddle.x));const prevY=ball.y;ball.x+=ball.vx*scale;ball.y+=ball.vy*scale;if(ball.x-ball.r<=0){ball.x=ball.r;ball.vx=Math.abs(ball.vx);}if(ball.x+ball.r>=W){ball.x=W-ball.r;ball.vx=-Math.abs(ball.vx);}if(ball.y-ball.r<=0){ball.y=ball.r;ball.vy=Math.abs(ball.vy);}if(ball.vy>0&&prevY+ball.r<=paddle.y&&ball.y+ball.r>=paddle.y&&ball.x>=paddle.x&&ball.x<=paddle.x+paddle.w){const offset=(ball.x-(paddle.x+paddle.w/2))/(paddle.w/2);ball.y=paddle.y-ball.r;ball.vy=-Math.abs(ball.vy);ball.vx=levelSpeed()*1.25*offset;}for(const b of bricks){if(!b.alive)continue;if(ball.x+ball.r>=b.x&&ball.x-ball.r<=b.x+b.w&&ball.y+ball.r>=b.y&&ball.y-ball.r<=b.y+b.h){b.hp--;ball.vy*=-1;score+=Math.round(25*settings.multiplier);if(b.hp<=0){b.alive=false;score+=Math.round(50*settings.multiplier);spawnPowerup(b);}setScore(score);updateStats();break;}}powerups.forEach(p=>p.y+=p.vy*scale);for(const p of powerups){if(p.y+11>=paddle.y&&p.y-11<=paddle.y+paddle.h&&p.x>=paddle.x&&p.x<=paddle.x+paddle.w){p.caught=true;applyPowerup(p);}}powerups=powerups.filter(p=>!p.caught&&p.y<H+20);if(!bricks.some(b=>b.alive))nextLevel();else if(ball.y-ball.r>H){lives--;powerups=[];paddle.w=settings.paddle;expandUntil=0;updateStats();if(lives<=0)end(false);else resetBall();}};
    const frame=now=>{if(!last)last=now;update(now-last);draw();last=now;raf=requestAnimationFrame(frame);};
    const reset=()=>{cancelAnimationFrame(raf);settings=DIFFICULTIES[difficulty()];level=1;paddle={x:(W-settings.paddle)/2,y:H-28,w:settings.paddle,h:12};lives=settings.lives;score=0;powerups=[];bricks=makeBricks();left=false;right=false;last=0;expandUntil=0;slowUntil=0;setScore(0);setGameEnded(false);setMessage('Clear all 10 levels. Catch falling power-ups with the paddle.');renderHighScores();resetBall();updateStats();raf=requestAnimationFrame(frame);};
    document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();if(e.key==='ArrowLeft')left=true;else right=true;}});document.addEventListener('keyup',e=>{if(e.key==='ArrowLeft')left=false;if(e.key==='ArrowRight')right=false;});canvas.addEventListener('pointermove',e=>{const r=canvas.getBoundingClientRect();const x=(e.clientX-r.left)*W/r.width;paddle.x=Math.max(0,Math.min(W-paddle.w,x-paddle.w/2));});
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  function startSimon(){
    const DIFFICULTIES={easy:{pads:4,speed:650,multiplier:1},medium:{pads:4,speed:450,multiplier:1.5},hard:{pads:6,speed:300,multiplier:2}};
    const COLORS=['#ef5350','#42a5f5','#66bb6a','#ffca28','#ab47bc','#ff7043'];
    const stage=canvas.parentElement;canvas.style.display='none';const boardEl=document.createElement('div');boardEl.className='vn-simon-board';stage.appendChild(boardEl);
    let settings,sequence,inputIndex,round,score,accepting,runId=0,pads=[];
    const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
    const light=async(index,duration=settings.speed*.62)=>{const pad=pads[index];pad?.classList.add('is-lit');await wait(duration);pad?.classList.remove('is-lit');};
    const play=async(id)=>{accepting=false;setMessage('Watch…');await wait(settings.speed*.55);for(const index of sequence){if(id!==runId)return;await light(index);await wait(settings.speed*.25);}if(id!==runId)return;inputIndex=0;accepting=true;setMessage('Your turn.');};
    const nextRound=()=>{round++;sequence.push(Math.floor(Math.random()*settings.pads));extraEl.textContent=`Round ${round} · ${settings.multiplier}× points`;play(runId);};
    const press=async index=>{if(!accepting||gameEnded)return;accepting=false;await light(index,120);if(index!==sequence[inputIndex]){setGameEnded(true);setMessage('Wrong pad — game over. Add your initials to save the score.');return;}inputIndex++;if(inputIndex===sequence.length){score+=Math.round(round*100*settings.multiplier);setScore(score);setMessage('Correct!');await wait(500);if(!gameEnded){accepting=false;nextRound();}}else accepting=true;};
    const reset=()=>{runId++;const id=runId;settings=DIFFICULTIES[difficulty()];sequence=[];inputIndex=0;round=0;score=0;accepting=false;setScore(0);setGameEnded(false);setMessage('Get ready…');extraEl.textContent=`Round 0 · ${settings.multiplier}× points`;renderHighScores();boardEl.innerHTML='';boardEl.classList.toggle('six',settings.pads===6);pads=Array.from({length:settings.pads},(_,i)=>{const pad=document.createElement('button');pad.type='button';pad.className='vn-simon-pad';pad.style.background=COLORS[i];pad.style.color=COLORS[i];pad.setAttribute('aria-label',`Sequence pad ${i+1}`);pad.addEventListener('click',()=>press(i));boardEl.appendChild(pad);return pad;});setTimeout(()=>{if(id===runId&&!gameEnded)nextRound();},700);};
    restart.onclick=reset;difficultyEl?.addEventListener('change',reset);reset();
  }

  if(app==='tetris')startTetris();else if(app==='jewels')startJewels();else if(app==='memory')startMemory();else if(app==='minesweeper')startMinesweeper();else if(app==='breakout')startBreakout();else if(app==='simon')startSimon();
})();
