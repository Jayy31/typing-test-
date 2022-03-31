const setOfWords = [
    "India, country that occupies the greater part of South Asia.",
    "Its capital is New Delhi, built in the 20th century just south of the historic hub of Old Delhi to serve as India’s administrative centre.",
    "With roughly one-sixth of the world’s total population, India is the second most populous country, after China.",
    "the Indus civilization—dominated the northwestern part of the subcontinent from about 2600 to 2000 BCE."
]

const msg= document.getElementById("msg");
const typeWords= document.getElementById("mywords");
const btn= document.getElementById("btn");
let startTime, endTime;

let playGame = () => {
    let randomNumber = Math.floor(Math.random()*ssetOfWords.length);
    msg.innerText = setOfWords[randomNumber];
}

btn.addEventListener('click',function(){
    if(this.innerText =='Start'){
        typeWords.disabled=false;
        playGame();
    }
})