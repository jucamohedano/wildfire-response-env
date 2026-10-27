import type {MapExtent} from './mapMosaicV2';
import type {TerrainMeshData} from './terrainSceneModel';

export function isImageryVoid(red:number,green:number,blue:number,alpha:number):boolean{
  return alpha===0||(red<=12&&green<=12&&blue<=12)
    ||(Math.abs(red-32)<=12&&Math.abs(green-26)<=12&&Math.abs(blue-38)<=12);
}

export function imageryGapMask(pixels:Uint8ClampedArray,width:number,height:number,extent:MapExtent,mesh:TerrainMeshData):Uint8Array{
  if(pixels.length!==width*height*4||width<=0||height<=0)throw new RangeError('Imagery dimensions do not match the pixel buffer.');
  const candidates=new Uint8Array(width*height);
  if(!mesh.support_mask)return candidates;
  const cellWidth=mesh.columns-1,cellHeight=mesh.rows-1,uncertain=new Uint8Array(cellWidth*cellHeight);
  for(let row=0;row<cellHeight;row++)for(let column=0;column<cellWidth;column++){
    uncertain[row*cellWidth+column]=Number(mesh.support_mask[row][column]!=='1'||mesh.support_mask[row][column+1]!=='1'
      ||mesh.support_mask[row+1][column]!=='1'||mesh.support_mask[row+1][column+1]!=='1');
  }
  const columns=new Int32Array(width);
  for(let column=0;column<width;column++){
    const east=extent[0]+(column+.5)*(extent[2]-extent[0])/width;
    columns[column]=east<mesh.extent[0]||east>=mesh.extent[2]?-1:Math.floor((east-mesh.extent[0])/(mesh.extent[2]-mesh.extent[0])*cellWidth);
  }
  let totalCandidates=0;
  for(let row=0;row<height;row++){
    const north=extent[3]-(row+.5)*(extent[3]-extent[1])/height;
    if(north<=mesh.extent[1]||north>mesh.extent[3])continue;
    const terrainRow=Math.floor((mesh.extent[3]-north)/(mesh.extent[3]-mesh.extent[1])*cellHeight);
    for(let column=0;column<width;column++){
      const terrainColumn=columns[column],index=row*width+column,offset=index*4;
      if(terrainColumn>=0&&uncertain[terrainRow*cellWidth+terrainColumn]
        &&isImageryVoid(pixels[offset],pixels[offset+1],pixels[offset+2],pixels[offset+3])){candidates[index]=1;totalCandidates++;}
    }
  }
  if(totalCandidates<256)return new Uint8Array(width*height);
  const queue=new Uint32Array(totalCandidates);
  for(let index=0;index<candidates.length;index++){
    if(candidates[index]!==1)continue;
    let head=0,tail=1;queue[0]=index;candidates[index]=2;
    while(head<tail){
      const current=queue[head++],column=current%width,row=Math.floor(current/width);
      if(column>0&&candidates[current-1]===1){candidates[current-1]=2;queue[tail++]=current-1;}
      if(column+1<width&&candidates[current+1]===1){candidates[current+1]=2;queue[tail++]=current+1;}
      if(row>0&&candidates[current-width]===1){candidates[current-width]=2;queue[tail++]=current-width;}
      if(row+1<height&&candidates[current+width]===1){candidates[current+width]=2;queue[tail++]=current+width;}
    }
    const keep=tail>=256?1:0;
    for(let item=0;item<tail;item++)candidates[queue[item]]=keep?3:0;
  }
  return candidates;
}

export interface FallbackColorField {columns:number;rows:number;blockSize:number;values:Float32Array;known:Uint8Array}

export function fallbackColorField(primary:Uint8ClampedArray,fallback:Uint8ClampedArray,mask:Uint8Array,width:number,height:number):FallbackColorField{
  if(primary.length!==width*height*4||fallback.length!==primary.length||mask.length!==width*height)throw new RangeError('Fallback imagery dimensions do not match.');
  const blockSize=32,columns=Math.ceil(width/blockSize),rows=Math.ceil(height/blockSize),size=columns*rows;
  const gaps=new Uint8Array(size),known=new Uint8Array(size),samples=new Uint32Array(size),sums=new Float64Array(size*3),values=new Float32Array(size*3);
  for(let row=0;row<height;row++)for(let column=0;column<width;column++)if(mask[row*width+column]===3)gaps[Math.floor(row/blockSize)*columns+Math.floor(column/blockSize)]=1;
  for(let row=0;row<height;row+=4)for(let column=0;column<width;column+=4){
    const index=row*width+column,offset=index*4,red=primary[offset],green=primary[offset+1],blue=primary[offset+2];
    if(mask[index]===3||red<45||green<45||blue<45||Math.max(red,green,blue)>200||blue<red+3
      ||isImageryVoid(red,green,blue,primary[offset+3])||isImageryVoid(fallback[offset],fallback[offset+1],fallback[offset+2],fallback[offset+3]))continue;
    const cell=Math.floor(row/blockSize)*columns+Math.floor(column/blockSize);samples[cell]++;
    for(let channel=0;channel<3;channel++)sums[cell*3+channel]+=primary[offset+channel]-fallback[offset+channel];
  }
  const queue=new Uint32Array(size);let head=0,tail=0;
  for(let row=0;row<rows;row++)for(let column=0;column<columns;column++){
    const cell=row*columns+column;if(!gaps[cell])continue;
    const totals=[0,0,0];let count=0;
    for(let deltaRow=-1;deltaRow<=1;deltaRow++)for(let deltaColumn=-1;deltaColumn<=1;deltaColumn++){
      const neighbourRow=row+deltaRow,neighbourColumn=column+deltaColumn;
      if(neighbourRow<0||neighbourRow>=rows||neighbourColumn<0||neighbourColumn>=columns)continue;
      const neighbour=neighbourRow*columns+neighbourColumn;count+=samples[neighbour];
      for(let channel=0;channel<3;channel++)totals[channel]+=sums[neighbour*3+channel];
    }
    if(count<4)continue;
    for(let channel=0;channel<3;channel++)values[cell*3+channel]=Math.max(-80,Math.min(80,totals[channel]/count));
    known[cell]=1;queue[tail++]=cell;
  }
  while(head<tail){
    const cell=queue[head++],column=cell%columns,row=Math.floor(cell/columns);
    for(const [deltaColumn,deltaRow] of [[-1,0],[1,0],[0,-1],[0,1]]){
      const nextColumn=column+deltaColumn,nextRow=row+deltaRow;
      if(nextColumn<0||nextColumn>=columns||nextRow<0||nextRow>=rows)continue;
      const next=nextRow*columns+nextColumn;if(!gaps[next]||known[next])continue;
      for(let channel=0;channel<3;channel++)values[next*3+channel]=values[cell*3+channel];
      known[next]=1;queue[tail++]=next;
    }
  }
  for(let pass=0;pass<2;pass++){
    const smoothed=new Float32Array(values);
    for(let row=0;row<rows;row++)for(let column=0;column<columns;column++){
      const cell=row*columns+column;if(!known[cell])continue;
      const totals=[values[cell*3]*2,values[cell*3+1]*2,values[cell*3+2]*2];let count=2;
      for(const [deltaColumn,deltaRow] of [[-1,0],[1,0],[0,-1],[0,1]]){
        const nextColumn=column+deltaColumn,nextRow=row+deltaRow;
        if(nextColumn<0||nextColumn>=columns||nextRow<0||nextRow>=rows)continue;
        const next=nextRow*columns+nextColumn;if(!known[next])continue;
        for(let channel=0;channel<3;channel++)totals[channel]+=values[next*3+channel];count++;
      }
      for(let channel=0;channel<3;channel++)smoothed[cell*3+channel]=totals[channel]/count;
    }
    values.set(smoothed);
  }
  return {columns,rows,blockSize,values,known};
}

export function sampleFallbackColor(field:FallbackColorField,column:number,row:number,result:Float32Array):void{
  const horizontal=Math.max(0,Math.min(field.columns-1,(column+.5)/field.blockSize-.5));
  const vertical=Math.max(0,Math.min(field.rows-1,(row+.5)/field.blockSize-.5));
  const left=Math.floor(horizontal),top=Math.floor(vertical),right=Math.min(field.columns-1,left+1),bottom=Math.min(field.rows-1,top+1);
  const fractionX=horizontal-left,fractionY=vertical-top;
  const northWest=top*field.columns+left,northEast=top*field.columns+right;
  const southWest=bottom*field.columns+left,southEast=bottom*field.columns+right;
  const northWestWeight=field.known[northWest]?(1-fractionX)*(1-fractionY):0,northEastWeight=field.known[northEast]?fractionX*(1-fractionY):0;
  const southWestWeight=field.known[southWest]?(1-fractionX)*fractionY:0,southEastWeight=field.known[southEast]?fractionX*fractionY:0;
  const total=northWestWeight+northEastWeight+southWestWeight+southEastWeight;
  if(total===0){result.fill(0);return;}
  for(let channel=0;channel<3;channel++)result[channel]=(field.values[northWest*3+channel]*northWestWeight
    +field.values[northEast*3+channel]*northEastWeight+field.values[southWest*3+channel]*southWestWeight
    +field.values[southEast*3+channel]*southEastWeight)/total;
}
