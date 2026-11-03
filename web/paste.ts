export const PASTE_IMAGE_TYPES=['image/png','image/jpeg','image/gif','image/webp'];
export const PASTE_IMAGE_MAX_BYTES=5*1024*1024;

export type ClipboardSnapshot={types:readonly string[];html:string;plain:string;files:File[];inCodeBlock?:boolean};
export type PastePlan={pasteText:'default'|'html'|'none';html?:string;upload:File[];rejected:File[];notice?:string};

const imgTag=/<img\b[^>]*>/gi;
const officeMarker=/urn:schemas-microsoft-com:office|<meta[^>]+(?:ProgId|Generator)[^>]+(?:Word|Excel|PowerPoint|Microsoft|WPS|Kingsoft)/i;

function imgSrc(tag:string){return tag.match(/\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i)?.slice(1).find(v=>v!==undefined)?.trim()||''}
// Images whose src survives saving: remote http(s) or same-site paths. Anything else (data:, blob:, file:, cid:, webkit-fake-url:) is dropped by cleanHtml.
function isKeptSrc(src:string){return /^https?:\/\//i.test(src)||/^\/(?!\/)/.test(src)}
function htmlText(html:string){return html.replace(/<!--[\s\S]*?-->/g,'').replace(/<(style|script|head|title)\b[\s\S]*?<\/\1>/gi,'').replace(/<[^>]+>/g,'').replace(/&nbsp;|&#160;/gi,' ').replace(/&[a-z#0-9]+;/gi,'x').trim()}
export function isPastableImage(file:File){return PASTE_IMAGE_TYPES.includes(file.type)&&file.size<=PASTE_IMAGE_MAX_BYTES}

export function planImagePaste({types,html,plain,files,inCodeBlock}:ClipboardSnapshot):PastePlan{
  const images=files.filter(file=>file.type.startsWith('image/'));
  const upload=images.filter(isPastableImage), rejected=images.filter(file=>!isPastableImage(file));
  const fileNames=new Set(files.map(file=>file.name));
  const plainIsFileNames=Boolean(plain.trim())&&plain.split(/\r?\n/).map(line=>line.trim()).filter(Boolean).every(line=>fileNames.has(line)||fileNames.has(line.split('/').pop()||''));
  const hasText=['vscode-editor-data','blocknote/html','text/markdown','text/html'].some(type=>types.includes(type))||(types.includes('text/plain')&&Boolean(plain)&&!plainIsFileNames);
  if(!files.length)return {pasteText:'default',upload:[],rejected:[]};
  if(!hasText){
    const notice=images.length<files.length?'仅支持粘贴图片文件，其他文件已忽略':undefined;
    return {pasteText:'none',upload,rejected,notice};
  }
  if(!images.length||inCodeBlock||types.includes('blocknote/html')||types.includes('vscode-editor-data'))return {pasteText:'default',upload:[],rejected:[]};
  const tags=html.match(imgTag)||[];
  const kept=tags.filter(tag=>isKeptSrc(imgSrc(tag))), lost=tags.filter(tag=>!isKeptSrc(imgSrc(tag)));
  // Browsers attach the copied <img> as a file too; the HTML already shows it, so uploading would duplicate it.
  if(kept.length)return {pasteText:'default',upload:[],rejected:[]};
  if(officeMarker.test(html)){
    // Office/WPS add a rendering of the whole selection; it is only the real image when nothing else was copied.
    if(!htmlText(html)&&tags.length<=1&&images.length===1)return {pasteText:'none',upload,rejected};
    return {pasteText:lost.length?'html':'default',html:lost.length?html.replace(imgTag,tag=>lost.includes(tag)?'':tag):undefined,upload:[],rejected:[],notice:lost.length?'文档中的图片无法随文字一起粘贴，请单独复制图片后粘贴':undefined};
  }
  if(lost.length)return {pasteText:'html',html:html.replace(imgTag,tag=>lost.includes(tag)?'':tag),upload,rejected};
  return {pasteText:'default',upload,rejected};
}
