clearInterval(perfectLoop); // stop old one if still running                                                                                 
                                                                                                                                               
let _block = 1;                                                                                                                              
const perfectLoop = setInterval(() => sendPerfect(_block++), 1000);                                                                          
                                                                                      

async function sendPerfect(blockCount) {                                                                                                     
  const payload = {
    isPerfect: true,
    instance: {
      x: 488.5368695706039,
      y: 614.0671328949019,                                                                                                                  
      height: 226.845,
      calWidth: 159.75,                                                                                                                      
    },          
    line: {
      x: 319.5,
      y: 825.9464666666667,                                                                                                                  
      collisionX: 639,
    },                                                                                                                                       
    blockCount, 
  };

  const res = await fetch("/api/update-score", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",                                                                                                    
      "uid": "tpHNyCfGg4PlBmVHdfcXikuM",
    },                                                                                                                                       
    body: JSON.stringify(payload),
  });                                                                                                                                        

  const data = await res.json();                                                                                                             
  console.log(`block ${blockCount}:`, data);
  return data;
}



//clearInterval(perfectLoop)                                                                                                                   

