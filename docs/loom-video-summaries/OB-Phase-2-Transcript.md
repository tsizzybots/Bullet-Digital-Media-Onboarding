00:00 Okay, next section after the client has completed the onboarding form survey is, uh, right, obviously they.
00:16 All the information they then put in there, uhm, where we've got, gets stored in the custom fields which exist in the service.
00:32 Bye. Okay, so, as you can see, it all drops in here. That's the, that's the initial stuff from the thing created from the agreement.
01:04 all these things. Consumer OVA's got it's own, but then there's different folders for the gyms, right? So, Facility, Info, Member Info, all drops in here.
01:16 Uhhh, that's obviously the item. Old ones you used to have, these are the new ones, and so on You can move some of these questions, On the kick-off call, so the digital specialist, on the account, start building a relationship, whilst the senior runs the call, currently, uhm, so basically, all the information
01:53 goes in here. It also gets appended to a Google Doc as well, but not all the information goes into that Google Doc.
02:08 You don't need it all in there for the Pickup Core. Uhm, I don't know if you can see all of this, but I'll show it so the screen grabs it.
02:24 You don't need it all in for the Pickup Core. Go to File, and here And there it is.
02:56 I had to push this guy through because the onboarding survey crashed for him.
03:12 So, he's not completed the rest, or some of it, doesn't know his fonts. Right, that's it. It's the unboarding information now, that's how it sticks to the contact currently, in the high-level sub-account.
03:39 So, it's all, all the information, right, is then spread between, in and out. On the kick-off, on the kick-off call dot, which is the summary of all the answers, everything.
03:53 Okay, and then, when the form is triggered, another workflow fires, so we're on board and survey complete.
04:09 Alright. So this one, first one, oh, f***ing hell, I've got it wrong then. Hang on. So the first webhook to Zapier, right, which I'll show what happens in a second.
04:32 This thing creates a new sub-account, it would seem, through Pavly. So I'm not sure if that's broken, because that's in two places.
04:48 hope we're not getting too, we're not getting too sub-accounts made currently, which Uhm, updates this stage to onboarding forms submitted.
05:04 Adds the note, client completed onboarding, moves the sign tag, uh, moves them from the follow-up of the pre-onboarder sending the link.
05:20 Looks at the spreadsheet, which is this one, and updates them to onboarding complete. Okay. Looks, well, finds them in the spreadsheet.
05:36 Updates that spreadsheet, gives them a wait, and then sends them an email to book Okay, now in this email, is, booked a kickoff call.
05:58 Alright. So, Process, like, after they've done this, and that survey is completed in here, they will automatically get to the calendar here, so it gives them a chance to book it straight away, and if they leave the page and don't book it, this is why this is, this got a half an hour wait on it, uhm, 
06:27 so if they leave the page and don't book it, we'll follow them up and make sure they do. Okay? So that's the way it is there.
06:38 Now, before we go on to Atlassian. After the kick-off call is booked, let's go into this webhook for the onboarding doc, right?
06:51 So, Back here. Onboarding survey complete.
07:09 We're in this one. Right, so, we are, where you going? Yep. We're catching the hook that will create the document.
07:28 And again, this could all be improved with the help of AI. We're finding the document template. Then we create the document from the template.
07:42 Then we're finding that created document. Step two. In Zapier. And then we're appending the text from the webhook into that document.
07:56 Okay. So this is exactly. Uh. Pulling all this information Snapping it.
08:12 Into. Okay. Document, which, sits in the place I'll show you Okay.
08:32 Uhm, like I said, the channel message is saying they've done their onboarding. Alright. Another example of a completed document like Is, Dun dun dun dun Onboarding Salvation Docs.
08:57 Just, it'll just drop in here. Right, this is the, the next one. So, sales handover notes, we have to get the sales team to paste these in to here, so we have a bit of scope before.
09:20 Uhm, this is space for notes, laid on the call, these are all notes, these are all notes, this is the information that goes in, space for notes, you know, all this bit, everything.
09:39 The red stuff he's got off the call. All research done before, about what they're at, if they're running ads, blah, blah, blah, Notes from the call in red.
09:52 And then on the call we'll basically run through this, with the goal of getting a gun, or, getting it locked in.
10:08 Okay, and then, so this is all the information, and then what we'll do is, should be being done, is, once we've had that call here, we'll just have a debrief and call it some name options for the offer that we feel are gonna fly and resonate with the, with the kind of gym they are.
10:42 And then try and sell our favorite one in, we send an email summarizing what was agreed after this to them to get absolute sign off before we go balls deep on setting everything up.
10:53 Offer components, we've gone through everything that they need. They can possibly offer to package up their front end offer on the call and we'd summarize this in here.
11:03 Then we'd work out if it's low ticket, you know, what the total value is, adding everything together. Say it's a 21 dayer and they're.
11:12 Say the price is, again, 200 quid a month and it's three weeks, for example, purposes, we'd work out what 75% of 200 quid is, add that to the total value of the consultation, the 30 quid, we'd add that on to the total value of the body scans if there's a price, if there's one that's 20 quid, you know
11:35 , and there's two of them in there, we'd add 40 quid on, so we'd get like a nice, like, total value of the, of the package, uhm, after.
11:44 Almost, kind of, selecting the most relevant anchor in terms of the membership. So they'll have different membership options, like, it's unlimited.
11:56 It that it, or the lock-in on that as well. There's a few considerations that go into the, the building or the anchoring of that initial membership price to, to discern the discount and then add on the consultations and body scans to create that total value.
12:11 And then, obviously, we work out what's the promo price, then we look at the percentage off, like, the pounds off.
12:16 On savings, like, did they want to bring a friend? How much did they bring a friend? Money back guarantee, yes or no?
12:21 And this will all factor in, so we take this information from here, like, when we've finalized it all and they'll, the digital specialists will put it into, you know, an email.
12:30 Uhm. To send to them to get, like, absolute sign-off on before, as I say, we go ball steep on setting everything up.
12:40 Then what we'll do in here is just identify the campaign flow, like, if it, we might be coming, and this is the thing that's changing a lot.
12:48 Are we going lead form first application to book appointment for a high-ticket client? Are we going landing page first to book appointment without any application?
13:00 Are we going, you know, for a, for a, for a for a large group class, lower-ticket style lead form to check out?
13:09 Are we going landing page to check out? Is there a calendar on the end? Like, what do we need to do?
13:15 And then currently what we do, the historic process is we, we check duplicate a funnel which is the closest match in terms of, you know, sequence and design.
13:26 Sam will go and do that and then the digital specialist will take, once confirmation comes in, they will go and take over that.
13:33 This is one of the things that is likely going to be changing if we can get it going. Go hard on the, on the AI studio, you know, landing page builder, uhm, important things for them to include, which the client, you know, really drove in on the call, uhm, or, you know, we thought of as we went through
13:50 and then, you know, things that we still need, confirming or may have missed on the call that we need absolute clarification on before moving forward.
14:01 And then they'll go and put this information into one of the templates on these links. Alright, uhm, so that's what's currently happening there.
14:15 Alright, and then they obviously move to kick off all, done, all that. Gone through this, yeah. Okay, so that happens then.
14:24 But we haven't got to the kick off call automation yet, so if we zoom out a bit and go back to after the onboarding survey is complete and now the client is sent to book the kick off call automation.
14:40 They obviously do it either immediately on the, on the portal thing that we've, that we've got, or they get followed up and do it through the email link to the, to the calendar.
14:53 up. Once they've done that, kick-off call box, this starts firing. Okay. So this then moves them across in the, in the signage compliance pipeline to kick-off call box.
15:11 Alright. We have the note on there, we have the tag, we remove the other tag, we send them an email confirmation.
15:22 Alright. And in this email confirmation is, The link to the folder. Confirmation of their Next Steps content.
15:40 Like, create a shared drive file that this has a custom link on it, right? So when, when that folder got created in one of the earlier Zapps, the link got shared to the custom field in here, which then is on their contact in the bullet Digital Media.
16:00 High-level sub-account, which then goes into this button. Okay? Uhm, so that, that's what goes on, goes on there. And as you can see, there's loads of things across loads of different places, uhm, with that.
16:16 That then happens, we wait five minutes, for some reason. Yeah, remove them from the onboarding survey, complete workflow, find the client in the sheet.
16:32 Update them, the kickoff call booked. Okay, send an inbound notification, saying booked in with a call, kickoff call, bosh, bosh, bosh, bosh, bosh.
16:48 And then this is where it gets naughty. Okay, so there's this one, add them to the Outstanding Elements tech follow-up workflow, which is complex and I'll come back to.
17:03 Start notification. Notification, tech team. This is just going straight to Sam in tech. Yep, sales channel I'll remind you to put their links in No, hang on.
17:23 This is for the DS. This is for the DS to put in there. You know, for the performance director even to sort of think about what digital specialists need to allocate.
17:40 But, we don't need that. We just do that every Monday. Uhm, and then, Webhook. Tazapio, right? So, we'll tackle this one first, because this then feeds into finance.
18:02 Okay, so that Webhook, Tazapio. And the purpose of this is now we have the kick-off of call date. And f*** is it?
18:13 Kick-off all booked. Looks like that's not even bloody live, so that's why it's not populating, because it must have been failing.
18:23 Oh, hang on. It's not that. It's not Webhook. Bear with. This is one of the things that we set up, and then leave.
18:40 Oh, Oh. There's more in here from, Onboarding survey complete No, What is the kicker called, This one.
19:28 This book, from my level on the kickoff called Boggs, is caught in We find the task in Asana, in the finance project.
19:40 Alright, and we update, the payment date on it, so finance knows when to charge them, in line. With when the kickoff call takes place, now this is irregular, and doesn't really work.
19:57 So what I do every Monday is go through the kickoff calls for the week. And then update the date manually.
20:03 Now the problem with this also is those kickoff call dates can But, yeah, that's kind of how it is. If you do, we'll, it's that late, we'll just charge them on the day anyway, and then just reschedule, like, the months to start in line with the, say, the originally booked the 10th, the subscription was
20:36 , the first payment would start there and then roll from the 10th every month, but then if they moved to, like, the 17th, we'd put the next payment on the 17th of the next month, if that makes sense.
20:48 So that's that, uh, payment one, and the other one is AddToOutstandingElementsTechFollowUp, which puts seatbelts on for, because this is where it gets really f****** fruity.
20:59 Uhm, okay. Okay, OutstandingElementsTechFollowUp, alright. So, to go into Confluence.
21:20 Kick off four books. I need Don't know what that's in there. It's got a double handle itself, but, then there's branches for Right.
21:42 So, I don't remember how I did this. Uhm, do they have their own ad account? Which we all want to know.
21:51 Yes, obviously they do. Down here, do they have their own business registration? I think Loaded, then. Right, so if we go, like, say yes to everything here.
22:00 Own ad account, yes. Business registration docs, yes. Headshot submitted. Yes. Uh, brand guideline submitted, yes.
22:15 Then all we need to get from them is access content and, like, Facebook account and page access. Their content, reminder, and Stripe information.
22:30 So it just goes, bam. That's the link from a, from a provider we use. I can't remember what it's called.
22:40 You might say Inere to be honest. Yeah, Leedsy, which we send to them and they just give us kind of one-click access to everything.
22:50 Takes out a lot of legwork. Stripe access. You need to go and follow these instructions if you don't have an account.
22:56 Make one. Alright. Images and videos, just a reminder. Deliver that link and get them, you know, sorted out. Okay. So if they said yes to the first three and no to the last well.
23:12 This would add in the brand guidelines that said no to. Okay. If they said no, you can see how it goes, basically.
23:21 So there's a different email that goes out depending on, what? What we've got and what we need. I'll run through it for the sake of the video but it might kill me.
23:34 So, own ad account is yes, business registration docs is yes, headshot is none. Right? Okay, so we need to get that.
23:42 Brand guidelines are submitted. Yes. Okay, so we just need access, contents, drive, headshot. If brand guidelines are not submitted then we just need access, contents, drive, headshot and guidelines.
23:53 So on and so forth. Uhm, ad account is, is yes, business registration docs is no, headshot is yes, brand guidelines is yes, we just need access, contents, drive, registration docs, brand guidelines is no, we need access, contents, drive, registration docs and guidelines.
24:10 Okay, if it is, an ad account is yes, business registration docs is no. Headshot is no, brand guidelines is yes, we need access, contents, drive, registration docs, headshot, brand guidelines is no, and that path, then we need access, contents, drive, registration docs, headshot and guidelines.
24:31 Right, the other side of it. own ad account is no, then, we don't ask for them to do the, the Leesy access thing.
24:42 Basically, it's a mirror path for that. So registration, ad account is no, and all of these. Registration docs is yes, headshot is submitted is yes, brand guidance is submitted, all we need then is to choose our content and stripe.
24:57 If, if it's business registration docs is yes, headshot is submitted is yes, brand guidance is no, we need content, stripe and guidelines.
25:06 If registration docs is yes, headshot submitted is no, brand guidance is yes, we need headshot and stripe. Content. If it's reg docs, yes, headshot is no, headshot no, brand guidance no, we need content, stripe, headshot, guidelines.
25:24 Alright. If we're going through, you know, ad account is no, business registration docs is no, headshot is yes, brand guidance is yes, we need content, stripes, registration docs docs, on that path here, brand guidelines is no, we need content, stripe, registration docs, business guideline, uhm, brand
25:44 guidelines. Uhm, same thing. Alakant is no, registration docs is no, headshots submitted is no, brand guidelines submitted is yes, we then need content, stripe, registration docs, headshots, if in the same path, brand guidelines is, is no, we need content, stripe, access, registration docs, headshots
26:08 . guidelines, and then, obviously, it fires out what we need with the, with the constants being content and stripe, alright, and then us picking up whatever else is missed so we get to everything.
26:25 Now, very very possible that there's a way better way to do this, uhm, but I don't know what it is.
26:35 Uhm, I mean there's, there's also things like, I mean, in, in high level if you, there's a launchpad option but it's confusing for them, where they can add their own, they can add their own new people to it, save what's doing it.
26:54 There's a couple of ways of doing You can connect the Stripe to it if you have one, but then if you give them that, then they're just going to ask for instructions on how to create a Stripe account if they don't have one.
27:05 Uhm, yeah. So, there's a lot of eventualities here. With a lot of to and fro from Sam in the early days.
27:19 Works, but it is very manual. With heavy assistance. From this. Okay, so that's what happens there. And then Sam will chase and have email conversations with Dom.
27:35 Until we have everything we need. Okay. Uhm. So that is then kick off called Books.
27:54 Yep. Yep, yep, And then kick off call complete is a manual move. So, go into the pipeline, we shift them across to kick off call complete, and it just updates the sheet here.
28:16 a conflicting thing with that, currently, is then when, uhm, when the payment is Because the order is changed. Uuuh, so then, and, where's payment received, where's the stride There we go.
28:41 So a new chart comes in, with a filter. I'm just going to shoot one, and just open I'm going to have to see if it exists currently.
29:04 This is probably broken. So much has changed. I'm going to go to Payment Received. Payment Received has now been, the order of it in this sheet is, is, is incorrect really.
29:17 I think. No, it's not. It's fine. I'm in Tanzania, near John. Uhm, okay, so that's where all that is at.
29:36 I'm going to pause and have a breather, and think about where else we're at with this. No, I think that's the crux of it for now.