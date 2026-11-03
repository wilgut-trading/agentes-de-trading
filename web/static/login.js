import {initializeApp} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import {getAuth,GoogleAuthProvider,signInWithPopup,signInWithRedirect,getRedirectResult} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";
const auth=getAuth(initializeApp(JSON.parse(document.getElementById("fb").textContent)));const m=document.getElementById("m");
async function seguir(c){const r=await fetch("/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idToken:await c.user.getIdToken()})});
if(r.ok)location.href="/";else m.textContent=(await r.json()).detalle}
getRedirectResult(auth).then(c=>c&&seguir(c)).catch(e=>m.textContent=e.message);
document.getElementById("g").onclick=()=>{const p=new GoogleAuthProvider();
/iPhone|iPad/.test(navigator.userAgent)?signInWithRedirect(auth,p):signInWithPopup(auth,p).then(seguir).catch(e=>m.textContent=e.message)};
