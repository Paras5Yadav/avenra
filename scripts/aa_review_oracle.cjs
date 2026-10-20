/* Independent OpenSSL/Node observer for synthetic wire interoperability tests. */
'use strict';
const crypto=require('node:crypto');
const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const from64=value=>Buffer.from(value,'base64');
if(input.mode==='review'){
 const parts=input.jwe.split('.');if(parts.length!==5)throw Error('serialization');
 const header=JSON.parse(Buffer.from(parts[0],'base64url'));
 if(header.alg!=='RSA-OAEP-256'||header.enc!=='A256GCM')throw Error('profile');
 const key=crypto.privateDecrypt({key:input.private_key,padding:crypto.constants.RSA_PKCS1_OAEP_PADDING,oaepHash:'sha256'},Buffer.from(parts[1],'base64url'));
 const cipher=crypto.createDecipheriv('aes-256-gcm',key,Buffer.from(parts[2],'base64url'));
 cipher.setAAD(Buffer.from(parts[0]));cipher.setAuthTag(Buffer.from(parts[4],'base64url'));
 process.stdout.write(Buffer.concat([cipher.update(Buffer.from(parts[3],'base64url')),cipher.final()]).toString());
}else if(input.mode==='fi'){
 const privateKey=crypto.createPrivateKey({key:Buffer.concat([Buffer.from('302e020100300506032b656e04220420','hex'),Buffer.from(input.private_key,'hex')]),type:'pkcs8',format:'der'});
 const results=[];
 for(const group of input.FI){
  let publicKey=from64(group.KeyMaterial.DHPublicKey.KeyValue);if(publicKey.length===32)publicKey=Buffer.concat([Buffer.from('302a300506032b656e032100','hex'),publicKey]);
  const secret=crypto.diffieHellman({privateKey,publicKey:crypto.createPublicKey({key:publicKey,type:'spki',format:'der'})});
  const local=from64(input.Nonce),remote=from64(group.KeyMaterial.Nonce);if(local.length!==32||remote.length!==32)throw Error('nonce length');
  const mixed=Buffer.from(local.map((v,i)=>v^remote[i]));
  const key=crypto.hkdfSync('sha256',secret,mixed.subarray(0,20),Buffer.alloc(0),32);
  for(const item of group.data){
   const encrypted=from64(item.encryptedFI),cipher=crypto.createDecipheriv('aes-256-gcm',key,mixed.subarray(20));
   cipher.setAuthTag(encrypted.subarray(-16));
   results.push({linkRefNumber:item.linkRefNumber,xml:Buffer.concat([cipher.update(encrypted.subarray(0,-16)),cipher.final()]).toString()});
  }
 }
 process.stdout.write(JSON.stringify(results));
}else if(input.mode==='jws'){
 const parts=input.signature.split('.'),header=JSON.parse(Buffer.from(parts[0],'base64url'));
 if(header.alg!=='RS256'||parts.length!==3)throw Error('signature profile');
 if(parts[1]&&Buffer.from(parts[1],'base64url').toString()!==input.body)throw Error('attached payload differs');
 const payload=header.b64===false?Buffer.from(input.body):Buffer.from(Buffer.from(input.body).toString('base64url'));
 const valid=crypto.verify('RSA-SHA256',Buffer.concat([Buffer.from(parts[0]+'.'),payload]),input.public_key,Buffer.from(parts[2],'base64url'));
 process.stdout.write(JSON.stringify({valid}));
}else throw Error('oracle mode');
